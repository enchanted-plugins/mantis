"""Lich M5 sandbox — POSIX Python runner.

Spawns one subprocess per witness. Caps are installed via `preexec_fn`
from `limits.apply_in_child` after fork, before exec. Environment is
scrubbed to deny outbound proxy traffic and the working directory is a
per-run tempdir deleted on return.

The child script is a fixed template: it loads the target module via
`importlib.util.spec_from_file_location`, invokes the named function
with the JSON-decoded witness, prints the result (if any) to stdout,
or prints the traceback to stderr and exits non-zero on failure.

stdout and stderr are bounded at 1 MB each to prevent memory blow-up
in the parent — cap markers trigger truncation, not a fail.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import signal as _signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:  # Package-relative first; script-path fallback for standalone execution.
    from . import _base
    from .. import limits
except ImportError:  # pragma: no cover — non-package invocation
    # sys.path-augment so both the scripts/ dir and runners/ dir are on the path.
    import sys as _sys
    from pathlib import Path as _Path
    _here = _Path(__file__).resolve()
    _runners_dir = _here.parent
    _scripts_dir = _runners_dir.parent
    for _p in (str(_runners_dir), str(_scripts_dir)):
        if _p not in _sys.path:
            _sys.path.insert(0, _p)
    import _base  # type: ignore
    import limits  # type: ignore


_STREAM_CAP_BYTES = 1 * 1024 * 1024  # 1 MB per stream

# Child-script template. Kept as a string (not a separate .py file) so the
# runner is one-file-deployable and the child has no filesystem footprint
# outside the per-run tempdir.
_CHILD_SCRIPT = '''\
import importlib.util, json, sys, traceback

target_file = sys.argv[1]
function_name = sys.argv[2]
witness_json = sys.argv[3]

try:
    witness = json.loads(witness_json)
    args = witness.get("args", [])
    kwargs = witness.get("kwargs", {})
except Exception:
    traceback.print_exc()
    sys.exit(2)

try:
    spec = importlib.util.spec_from_file_location("sandbox_target", target_file)
    if spec is None or spec.loader is None:
        print("sandbox: could not load target", file=sys.stderr)
        sys.exit(3)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, function_name, None)
    if fn is None:
        print(f"sandbox: no function {function_name!r} in target", file=sys.stderr)
        sys.exit(4)
except Exception:
    traceback.print_exc()
    sys.exit(5)

try:
    result = fn(*args, **kwargs)
    try:
        print(json.dumps({"ok": True, "result": repr(result)}))
    except Exception:
        print(json.dumps({"ok": True, "result": "<unprintable>"}))
    sys.exit(0)
except BaseException:
    # BaseException covers SystemExit/KeyboardInterrupt too — we want a
    # traceback for anything that terminates the call abnormally.
    traceback.print_exc()
    sys.exit(1)
'''


def _scrubbed_env() -> dict:
    """Minimal env: deny proxies, pin PATH, drop most of the host env."""
    return {
        "PATH": "/usr/bin:/bin",
        "no_proxy": "*",
        "NO_PROXY": "*",
        # Deliberate: HTTP_PROXY / HTTPS_PROXY not set. Python picks them
        # up from env only if set; absence is the scrub.
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
    }


def _truncate(data: bytes) -> str:
    if len(data) > _STREAM_CAP_BYTES:
        head = data[:_STREAM_CAP_BYTES]
        return head.decode("utf-8", errors="replace") + "\n...[truncated]\n"
    return data.decode("utf-8", errors="replace")


def _signal_name_from_exit(rc: int) -> str | None:
    """POSIX: negative rc means killed by -rc. Map to symbolic name."""
    if rc >= 0:
        return None
    try:
        return _signal.Signals(-rc).name
    except (ValueError, AttributeError):
        return f"SIG{-rc}"


class PosixPythonRunner:
    """POSIX runner. Raises NotImplementedError on non-POSIX — callers
    must platform-guard via bridge.platform_guard.check first."""

    def __init__(self) -> None:
        if platform.system() == "Windows":
            raise NotImplementedError(
                "PosixPythonRunner requires POSIX; use bridge.wsl.run_in_wsl."
            )

    def run(
        self,
        target_file: str,
        function_name: str,
        witness: dict,
    ) -> _base.RunResult:
        # Per-run sandbox tempdir — cwd for the child, scrubbed on exit.
        workdir = tempfile.mkdtemp(prefix="lich_m5_")
        child_path = Path(workdir) / "_sandbox_child.py"
        child_path.write_text(_CHILD_SCRIPT, encoding="utf-8")

        witness_json = json.dumps(witness)
        cmd = [sys.executable, str(child_path), target_file, function_name, witness_json]

        start = time.monotonic()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=workdir,
                env=_scrubbed_env(),
                preexec_fn=limits.apply_in_child,  # noqa: PLW1509 — deliberate
                close_fds=True,
            )
            # Parent-side wall-clock fence — backstops the child's own alarm
            # in case the child blocks before signal.alarm lands.
            try:
                stdout_b, stderr_b = proc.communicate(
                    timeout=limits.SIGNAL_ALARM_SEC + 5
                )
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_b, stderr_b = proc.communicate()
                exit_code = -9  # SIGKILL analogue
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

        duration_ms = int((time.monotonic() - start) * 1000)
        signal_name = _signal_name_from_exit(exit_code)
        # Normalize negative exit to 128+N so the JSON shape is uniform
        # across POSIX/WSL backends; preserve the signal in signal_name.
        normalized_exit = 128 + (-exit_code) if exit_code < 0 else exit_code

        return _base.RunResult(
            exit_code=normalized_exit,
            stdout=_truncate(stdout_b or b""),
            stderr=_truncate(stderr_b or b""),
            duration_ms=duration_ms,
            signal_name=signal_name,
        )
