"""Mantis M5 sandbox — runner protocol.

Each backend (POSIX, WSL, future Job Objects) implements this protocol.
The orchestrator selects a backend via `bridge.platform_guard.check` and
then calls `run(...)` once per witness.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class RunResult:
    """The raw outcome of one sandboxed invocation, pre-classification.

    `signal_name` is the symbolic name ("SIGALRM" / "SIGKILL" / etc.) or
    None if the child exited cleanly or via non-signal exit. `exit_code`
    is the negative-signal form on POSIX (-N) normalized to 128+N here,
    with the concrete signal carried in `signal_name` — keeps the JSON
    shape portable across backends.
    """
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    signal_name: str | None


class Runner(Protocol):
    """Structural type for sandbox backends."""

    def run(
        self,
        target_file: str,
        function_name: str,
        witness: dict,
    ) -> RunResult:
        ...
