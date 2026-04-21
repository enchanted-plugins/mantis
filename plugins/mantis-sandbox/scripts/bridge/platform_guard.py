"""Mantis M5 sandbox — platform guard.

Single probe per process: decide which backend the sandbox should use.

* `posix` — Linux / macOS (or BSD best-effort); `resource.setrlimit` is
  available in-process, so Agent 3's `PosixPythonRunner` runs directly.
* `wsl` — Windows host with WSL installed AND a default distro whose
  `python3` can import `resource` + `signal`. Agent 3 routes witnesses
  through `bridge.wsl.run_in_wsl`.
* `unsupported` — Windows host without usable WSL, or any probe failure.
  Agent 3 emits `platform-unsupported` per CLAUDE.md behavioral contract 2.

The probe result is cached in a module-level global so repeated `check()`
calls from Agent 3 (one per witness loop) don't re-spawn `wsl.exe`.
"""

from __future__ import annotations

import platform
import subprocess
from typing import Optional

# Cache: `None` means "not probed yet"; any dict is the memoised result.
_CACHED: Optional[dict] = None

# How long to wait on the two WSL probes. These run once per process; short
# timeouts keep the first-witness latency low when WSL is misconfigured.
_STATUS_TIMEOUT_S = 2
_CAPABILITY_TIMEOUT_S = 4

# Canonical substring from `wsl.exe --status` when WSL is absent. Microsoft
# ships the message UTF-16LE-encoded (observed on Win11 26200); we decode
# defensively in `_decode_wsl_output`.
_NOT_INSTALLED_MARKER = "Windows Subsystem for Linux is not installed"


def _decode_wsl_output(raw: bytes) -> str:
    """WSL CLI emits UTF-16LE on some Windows builds, UTF-8 on others.

    Try UTF-16LE first (the observed Win11 behaviour); fall back to UTF-8
    with replacement so a decode miss never crashes the probe.
    """
    if not raw:
        return ""
    # UTF-16LE signal: lots of interleaved NULs between ASCII bytes.
    if b"\x00" in raw[:64]:
        try:
            return raw.decode("utf-16-le", errors="replace")
        except Exception:
            pass
    return raw.decode("utf-8", errors="replace")


def _probe_windows() -> dict:
    """Run the two-step WSL probe. Returns the public `check()` dict."""
    # Step 1: is WSL installed at all?
    try:
        status = subprocess.run(
            ["wsl.exe", "--status"],
            capture_output=True,
            timeout=_STATUS_TIMEOUT_S,
            check=False,
        )
    except FileNotFoundError:
        return {"supported": False, "backend": "unsupported",
                "reason": "wsl.exe not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"supported": False, "backend": "unsupported",
                "reason": f"wsl.exe --status timed out after {_STATUS_TIMEOUT_S}s"}
    except OSError as exc:
        return {"supported": False, "backend": "unsupported",
                "reason": f"wsl.exe --status failed: {exc}"}

    combined = _decode_wsl_output(status.stdout) + _decode_wsl_output(status.stderr)
    if _NOT_INSTALLED_MARKER in combined:
        return {"supported": False, "backend": "unsupported",
                "reason": "WSL is not installed (wsl.exe --status reports unavailable)"}
    if status.returncode != 0:
        return {"supported": False, "backend": "unsupported",
                "reason": f"wsl.exe --status exited {status.returncode}: "
                          f"{combined.strip()[:200] or '<no output>'}"}

    # Step 2: default distro usable? Ask it to import the POSIX modules the
    # sandbox depends on. If this succeeds we know the Linux-side runner can
    # install the caps inside the WSL child.
    try:
        cap = subprocess.run(
            ["wsl.exe", "-e", "python3", "-c", "import resource, signal; print('ok')"],
            capture_output=True,
            timeout=_CAPABILITY_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"supported": False, "backend": "unsupported",
                "reason": f"WSL python3 capability probe timed out "
                          f"after {_CAPABILITY_TIMEOUT_S}s"}
    except OSError as exc:
        return {"supported": False, "backend": "unsupported",
                "reason": f"WSL python3 capability probe failed: {exc}"}

    cap_stdout = _decode_wsl_output(cap.stdout).strip()
    if cap.returncode == 0 and cap_stdout == "ok":
        return {"supported": True, "backend": "wsl",
                "reason": "WSL default distro has python3 with resource+signal"}

    cap_stderr = _decode_wsl_output(cap.stderr).strip()[:200]
    return {"supported": False, "backend": "unsupported",
            "reason": f"WSL python3 capability probe failed "
                      f"(exit {cap.returncode}): {cap_stderr or cap_stdout or '<no output>'}"}


def check() -> dict:
    """Return the sandbox backend decision for this host.

    Shape:
        {"supported": bool, "backend": "posix"|"wsl"|"unsupported",
         "reason": str}

    Cached for the process lifetime after first call.
    """
    global _CACHED
    if _CACHED is not None:
        return _CACHED

    system = platform.system()
    if system in ("Linux", "Darwin"):
        _CACHED = {"supported": True, "backend": "posix", "reason": ""}
    elif system == "Windows":
        _CACHED = _probe_windows()
    else:
        # BSD / SunOS / AIX / etc. — the `resource` module exists on all
        # reasonable POSIX kernels; best-effort posix routing with the
        # system name in the reason so misroutes are diagnosable.
        _CACHED = {"supported": True, "backend": "posix",
                   "reason": f"best-effort posix on {system}"}
    return _CACHED


def _reset_cache_for_tests() -> None:
    """Test-only hook: invalidate the memoised probe result."""
    global _CACHED
    _CACHED = None
