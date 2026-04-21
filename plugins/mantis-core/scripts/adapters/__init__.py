"""Language adapters for Mantis M1.

Each adapter follows the same contract so the dispatcher below routes a
file by extension to the correct backend:

    LANG: str                    — human-readable language id (e.g. "go")
    FILE_EXTENSIONS: list[str]   — extensions this adapter owns
    detect() -> Optional[str]    — absolute path to tool binary, or None
    analyze(file_path) -> list[Flag]  — M1 flags or [] (never None, never raises)

Adapters are advisory: when the underlying linter is absent, `detect()`
returns None and `analyze()` returns []. Security-bucket rules NEVER route
to M1 — each adapter carries a hard security guard mirroring ruff_adapter.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, List

from . import cpp, go, java, ruby, rust, semgrep, shell

# Polyglot adapter (semgrep) layers on top of any primary backend.
_POLYGLOT = (semgrep,)

_BY_EXTENSION: dict[str, tuple] = {}
for _mod in (go, rust, java, cpp, ruby, shell):
    for _ext in _mod.FILE_EXTENSIONS:
        _BY_EXTENSION[_ext.lower()] = (_mod,)


def dispatch(file_path: str) -> List[Callable]:
    """Returns a list of `analyze` callables applicable to this file."""
    ext = Path(file_path).suffix.lower()
    primary = _BY_EXTENSION.get(ext, ())
    return [m.analyze for m in (*primary, *_POLYGLOT)]


# Back-compat exports kept for Agent 4's original surface.
from .go import analyze as analyze_go
from .rust import analyze as analyze_rust

__all__ = [
    "cpp", "go", "java", "ruby", "rust", "semgrep", "shell",
    "dispatch", "analyze_go", "analyze_rust",
]
