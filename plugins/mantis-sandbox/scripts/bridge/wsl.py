"""Mantis M5 sandbox — WSL delegation backend.

Given a Windows host with WSL installed, execute the child runner inside
WSL's Linux Python so the 6 resource caps + `signal.alarm` contract holds
for real. The parent-side `wsl.exe` invocation is a thin wrapper around
`subprocess.run`; all cap enforcement lives in `_child_runner.CHILD_SCRIPT`
piped via stdin.

Path translation: Windows paths (`C:\\git\\foo\\bar.py`) are mapped to the
WSL `/mnt/<letter>/...` form so `importlib.util.spec_from_file_location`
can load the target from the cross-mounted drive.

Env scrubbing: the WSL child runs under `env -i` with a minimal whitelist
matching Agent 3's `_scrubbed_env` — deny proxies, pin PATH, UTF-8 locale.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import PureWindowsPath

from ._child_runner import CHILD_SCRIPT


def _windows_to_wsl(path: str) -> str:
    """Translate `C:\\git\\foo\\bar.py` -> `/mnt/c/git/foo/bar.py`.

    Uses `PureWindowsPath` to parse the drive + parts reliably (handles
    both backslash and forward-slash separators, and mixed case drives).
    Non-drive paths (UNC, bare relative) fall through unchanged — the
    caller is expected to pass absolute paths from M1's flag records.
    """
    pw = PureWindowsPath(path)
    drive = pw.drive  # e.g. "C:" or "" for UNC/relative
    if not drive or not drive.endswith(":"):
        # Not a drive-letter absolute path; return as-is (best-effort).
        return str(pw).replace("\\", "/")
    letter = drive[0].lower()
    # `pw.parts[0]` is the drive+root ("C:\\"); skip it.
    rest = "/".join(pw.parts[1:]) if len(pw.parts) > 1 else ""
    return f"/mnt/{letter}/{rest}" if rest else f"/mnt/{letter}/"


# Marker -> signal-name mapping parsed out of stderr. The WSL child exits
# non-zero via its own signal delivery, so unlike POSIX we infer from
# stderr text rather than a negative returncode.
_SIGNAL_MARKERS = (
    ("SIGALRM", re.compile(r"\bSIGALRM\b|\bAlarm clock\b", re.IGNORECASE)),
    ("MemoryError", re.compile(r"\bMemoryError\b")),
    ("SIGKILL", re.compile(r"\bKilled\b")),
    ("SIGBUS", re.compile(r"\bBus error\b", re.IGNORECASE)),
    ("SIGXFSZ", re.compile(r"\bFile size limit exceeded\b", re.IGNORECASE)),
)

# Cap the env whitelist on the WSL side. `env -i` starts from empty, so we
# re-inject only what the child needs. Mirrors runners/python.py::_scrubbed_env.
_WSL_ENV_WHITELIST = [
    "HOME=/tmp",
    "PATH=/usr/bin:/bin",
    "no_proxy=*",
    "NO_PROXY=*",
    "LANG=C.UTF-8",
    "LC_ALL=C.UTF-8",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUNBUFFERED=1",
]


def _detect_signal(stderr: str) -> str | None:
    """Scan stderr for kill/cap markers; return the first match or None."""
    if not stderr:
        return None
    for name, pattern in _SIGNAL_MARKERS:
        if pattern.search(stderr):
            return name
    return None


def run_in_wsl(
    target_file: str,
    function_name: str,
    witness_json: str,
    timeout_s: int = 10,
) -> dict:
    """Execute one sandboxed witness inside WSL.

    Args:
        target_file: absolute Windows path to the .py file under test.
        function_name: symbol to call inside the target module.
        witness_json: JSON-serialised witness `{"args": [...], "kwargs": {...}}`.
        timeout_s: parent-side wall-clock fence (backstop; the child also
            arms `signal.alarm(ALARM_S)`).

    Returns:
        `{exit_code, stdout, stderr, duration_ms, signal}` with `signal`
        set to a symbolic name ("SIGALRM", "MemoryError", ...) or None.

    On `wsl.exe`-level failure (distro crashed, wsl.exe missing) the
    return shape is preserved with `exit_code=-1` and a stderr message
    prefixed `WSL invocation failed:` — Agent 3's `outcome.classify`
    routes this to `sandbox-error`, not `confirmed-bug`.
    """
    wsl_target = _windows_to_wsl(target_file)

    # Parse witness up front so a malformed JSON doesn't get laundered
    # into the WSL child as a parse-error-inside-the-sandbox.
    try:
        witness = json.loads(witness_json)
    except json.JSONDecodeError as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"WSL invocation failed: witness JSON invalid: {exc}",
            "duration_ms": 0,
            "signal": None,
        }

    stdin_payload = json.dumps({
        "target_file": wsl_target,
        "function_name": function_name,
        "witness": witness,
    })

    # Build argv: `wsl.exe -e env -i <whitelist> python3 -`. The `-` tells
    # python3 to read the script from stdin; but stdin also carries the
    # witness payload. Resolved by concatenating a header:
    # [script body] + "\n# ---END-SCRIPT---\n" + [witness json]?
    # Simpler: pass the script via `python3 -c <body>` and reserve stdin
    # for the witness JSON. Command-line length is bounded (~100 KB on
    # WSL); the script is ~2 KB, well under the limit.
    argv = [
        "wsl.exe", "-e", "env", "-i",
        *_WSL_ENV_WHITELIST,
        "python3", "-c", CHILD_SCRIPT,
    ]

    start = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            input=stdin_payload.encode("utf-8"),
            capture_output=True,
            timeout=timeout_s + 5,  # parent fence beyond child's signal.alarm
            check=False,
        )
    except FileNotFoundError:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": "WSL invocation failed: wsl.exe not found on PATH",
            "duration_ms": int((time.monotonic() - start) * 1000),
            "signal": None,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "exit_code": -1,
            "stdout": (exc.stdout or b"").decode("utf-8", errors="replace"),
            "stderr": (
                f"WSL invocation failed: parent timeout after {timeout_s + 5}s\n"
                + (exc.stderr or b"").decode("utf-8", errors="replace")
            ),
            "duration_ms": int((time.monotonic() - start) * 1000),
            "signal": "SIGALRM",
        }
    except OSError as exc:
        return {
            "exit_code": -1,
            "stdout": "",
            "stderr": f"WSL invocation failed: {exc}",
            "duration_ms": int((time.monotonic() - start) * 1000),
            "signal": None,
        }

    duration_ms = int((time.monotonic() - start) * 1000)
    stdout = proc.stdout.decode("utf-8", errors="replace") if proc.stdout else ""
    stderr = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""

    # If wsl.exe itself failed (distro crashed, service stopped) the exit
    # code is typically non-zero AND no child traceback is present. We
    # detect this heuristically: exit != 0 with no recognisable Python
    # traceback + a WSL-level error message in stderr.
    if proc.returncode != 0 and "Traceback" not in stderr and (
        "WSL" in stderr or "wsl" in stderr.lower()
    ) and "File " not in stderr:
        return {
            "exit_code": -1,
            "stdout": stdout,
            "stderr": f"WSL invocation failed: {stderr.strip() or '<no stderr>'}",
            "duration_ms": duration_ms,
            "signal": None,
        }

    return {
        "exit_code": proc.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "duration_ms": duration_ms,
        "signal": _detect_signal(stderr),
    }
