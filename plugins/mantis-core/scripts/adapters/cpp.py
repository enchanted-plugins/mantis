"""clang-tidy adapter for C/C++ M1 coverage.

clang-tidy emits diagnostics on stderr in the form:
    <path>:<line>:<col>: warning: <message> [<rule-id>]

We parse that (stdlib has no YAML) and map rule IDs via
shared/rules/languages/cpp.json. When no compilation database is present,
we invoke with `--` to skip compile-db lookup (works for isolated files).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from m1_walker import Flag

from ._base import (
    _log,
    detect_binary,
    is_security_bucket,
    load_registry,
    run_subprocess,
)

LANG = "cpp"
FILE_EXTENSIONS = [".c", ".cc", ".cpp", ".cxx", ".h", ".hpp"]

_DIAG_RE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):(?P<col>\d+):\s+"
    r"(?P<level>warning|error|note):\s+"
    r"(?P<msg>.+?)\s+\[(?P<rule>[a-z0-9\-\.,]+)\]\s*$",
    re.MULTILINE,
)


def detect() -> Optional[str]:
    return detect_binary("clang-tidy")


def _severity(rule_id: str, level: str) -> str:
    if level == "error":
        return "HIGH"
    if rule_id.startswith(("bugprone-", "cert-", "clang-analyzer-")):
        return "HIGH"
    if rule_id.startswith(("misc-", "performance-")):
        return "MED"
    return "LOW"


def _find_compile_db(source_file: str) -> Optional[Path]:
    path = Path(source_file).resolve()
    for parent in path.parents:
        for rel in ("compile_commands.json", "build/compile_commands.json"):
            cand = parent / rel
            if cand.is_file():
                return cand
    return None


def analyze(file_path: str, *, timeout_s: int = 15) -> list[Flag]:
    binary = detect()
    if not binary:
        return []
    db = _find_compile_db(file_path)
    cmd = [binary, file_path]
    if db:
        cmd += ["-p", str(db.parent)]
    else:
        cmd += ["--", "-std=c++17"]
    proc = run_subprocess(cmd, timeout_s=timeout_s)
    if not proc:
        return []
    output = (proc.stderr or "") + "\n" + (proc.stdout or "")
    registry = load_registry(LANG)
    flags: list[Flag] = []
    seen: set[tuple] = set()
    abs_target = str(Path(file_path).resolve()).replace("\\", "/")
    for m in _DIAG_RE.finditer(output):
        rule_id = m.group("rule").split(",", 1)[0]
        path_hit = m.group("path").replace("\\", "/")
        if not abs_target.endswith(path_hit) and not path_hit.endswith(Path(file_path).name):
            continue
        bucket, _ = registry.get(rule_id, ("unmapped", "MED"))
        if is_security_bucket(bucket) or bucket != "correctness_m1":
            continue
        line = int(m.group("line"))
        key = (line, rule_id)
        if key in seen:
            continue
        seen.add(key)
        flags.append(Flag(
            file=file_path,
            line=line,
            function="<unknown>",
            rule_id=f"CPP-{rule_id}",
            flag_class=rule_id,
            severity=_severity(rule_id, m.group("level")),
            witness_hints={"message": m.group("msg")},
            needs_M5_confirmation=False,
            m1_confidence=0.8,
        ))
    return flags
