"""Mantis M5 sandbox — platform bridge.

Exports the two entry points Agent 3's `sandbox.py` consumes:

* `check()` — probe the host once, return backend selection.
* `run_in_wsl(...)` — delegate a sandboxed invocation into WSL when the host
  is Windows and WSL is available. On POSIX hosts this import is harmless
  (the module only uses stdlib); on Windows without WSL the function should
  never be called because `check()` reports `unsupported`.

The bridge is the Windows-honesty layer: it lets the M5 pipeline opt into
real sandboxed execution when WSL is present and emit `platform-unsupported`
otherwise — never silently pretend M5 ran.
"""

from __future__ import annotations

from .platform_guard import check
from .wsl import run_in_wsl

__all__ = ["check", "run_in_wsl"]
