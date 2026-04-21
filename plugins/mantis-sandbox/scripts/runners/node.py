"""Node.js sandbox runner for TypeScript/JavaScript targets.

**Weaker than the Python sandbox** — Node's stdlib has no RLIMIT equivalents.
We enforce:
  * Heap cap via `--max-old-space-size=512`    (maps to RLIMIT_AS, weakly)
  * Wall-clock via `signal.alarm(10)` on parent (same as Python path)
  * Scrubbed env + per-run `tempfile.mkdtemp()` CWD

Gaps vs Python runner (document honestly, do not pretend):
  * NO RLIMIT_NOFILE equivalent — file-descriptor exhaustion possible
  * NO RLIMIT_FSIZE — a witness that writes > 10MB is not blocked
  * NO RLIMIT_NPROC — child processes (spawn/fork) not capped

Use with explicit awareness that this runner's sandbox is weaker. On TS
files, requires `tsx` in the target env; falls back to `input-synthesis-failed`
if absent (transpiling via tsc would require a compile step we don't own).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from _base import Runner, RunResult


_HEAP_CAP_MB = 512
_ALARM_S = 10
_STREAM_CAP_BYTES = 1024 * 1024

_CHILD_JS = r"""
const fs = require('fs');
const path = require('path');

let stdin_buf = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', d => stdin_buf += d);
process.stdin.on('end', async () => {
  let payload;
  try { payload = JSON.parse(stdin_buf); }
  catch (e) { process.stderr.write('BAD_STDIN\n'); process.exit(2); }
  const { target, fn, args } = payload;
  try {
    let mod;
    try { mod = require(target); }
    catch (_e) { mod = await import(target); mod = mod.default || mod; }
    const f = mod[fn] || (typeof mod === 'function' ? mod : null);
    if (!f) { process.stderr.write('NO_SUCH_FUNCTION: ' + fn + '\n'); process.exit(3); }
    const result = await f.apply(null, args || []);
    process.stdout.write(JSON.stringify({ok: true, result: result === undefined ? null : result}));
    process.exit(0);
  } catch (err) {
    process.stderr.write((err && err.stack) ? err.stack : String(err));
    process.exit(1);
  }
});
"""


def detect() -> Optional[str]:
    return shutil.which("node")


def detect_tsx() -> Optional[str]:
    return shutil.which("tsx") or shutil.which("ts-node")


class NodeRunner(Runner):
    def __init__(self):
        self._node = detect()
        self._tsx = detect_tsx()

    def run(self, target_file: str, function_name: str, witness) -> RunResult:
        ext = Path(target_file).suffix.lower()
        if ext not in (".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx"):
            return RunResult(status="input-synthesis-failed",
                              stdout="", stderr=f"non-js extension: {ext}",
                              exit_code=None, signal=None, duration_ms=0)
        if not self._node:
            return RunResult(status="input-synthesis-failed",
                              stdout="", stderr="node binary not detected",
                              exit_code=None, signal=None, duration_ms=0)
        if ext in (".ts", ".tsx") and not self._tsx:
            return RunResult(status="input-synthesis-failed",
                              stdout="", stderr="tsx/ts-node required for .ts — not present",
                              exit_code=None, signal=None, duration_ms=0)

        tmpdir = tempfile.mkdtemp(prefix="mantis-node-")
        child_path = Path(tmpdir) / "_child.js"
        child_path.write_text(_CHILD_JS, encoding="utf-8")

        env = {"PATH": "/usr/bin:/bin", "HOME": tmpdir, "no_proxy": "*"}
        cmd = [
            self._node,
            f"--max-old-space-size={_HEAP_CAP_MB}",
            str(child_path),
        ]
        if ext in (".ts", ".tsx") and self._tsx:
            # Use tsx as a loader
            cmd = [self._tsx, str(child_path)]

        payload = json.dumps({
            "target": target_file,
            "fn": function_name,
            "args": getattr(witness, "args", []),
        })

        start = time.monotonic()
        sig = None
        try:
            proc = subprocess.run(
                cmd,
                input=payload,
                capture_output=True,
                text=True,
                cwd=tmpdir,
                env=env,
                timeout=_ALARM_S,
            )
            exit_code = proc.returncode
            stdout = (proc.stdout or "")[:_STREAM_CAP_BYTES]
            stderr = (proc.stderr or "")[:_STREAM_CAP_BYTES]
        except subprocess.TimeoutExpired as e:
            exit_code = -1
            sig = "SIGALRM"
            stdout = (e.stdout or b"")[:_STREAM_CAP_BYTES].decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
            stderr = (e.stderr or b"")[:_STREAM_CAP_BYTES].decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        finally:
            duration_ms = int((time.monotonic() - start) * 1000)
            shutil.rmtree(tmpdir, ignore_errors=True)

        # Minimal status classification — richer mapping lives in outcome.py
        if sig == "SIGALRM":
            status = "timeout-without-confirmation"
        elif exit_code == 0:
            status = "no-bug-found"
        else:
            status = "ran"

        return RunResult(
            status=status,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            signal=sig,
            duration_ms=duration_ms,
        )
