"""shellcheck adapter for shell-script M1 coverage.

`shellcheck --format=json1` emits `{comments: [...]}` with `code` (2xxx) +
`level`. We map SC codes via the registry.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from m1_walker import Flag

from ._base import (
    _log,
    detect_binary,
    is_security_bucket,
    load_registry,
    run_subprocess,
)

LANG = "shell"
FILE_EXTENSIONS = [".sh", ".bash"]


def detect() -> Optional[str]:
    return detect_binary("shellcheck")


def _severity(level: str) -> str:
    return {
        "error": "HIGH",
        "warning": "MED",
        "info": "LOW",
        "style": "LOW",
    }.get(level.lower(), "MED")


def analyze(file_path: str, *, timeout_s: int = 5) -> list[Flag]:
    binary = detect()
    if not binary:
        return []
    proc = run_subprocess([binary, "--format=json1", file_path], timeout_s=timeout_s)
    if not proc or not proc.stdout:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _log({"event": "shellcheck-json-parse-failed", "error": str(e)})
        return []
    registry = load_registry(LANG)
    flags: list[Flag] = []
    for com in data.get("comments", []):
        code = com.get("code")
        if code is None:
            continue
        rule_id = f"SC{code}"
        bucket, _ = registry.get(rule_id, ("unmapped", "MED"))
        if is_security_bucket(bucket) or bucket != "correctness_m1":
            continue
        flags.append(Flag(
            file=file_path,
            line=int(com.get("line") or 0),
            function="<unknown>",
            rule_id=f"SHELL-{rule_id}",
            flag_class=rule_id.lower(),
            severity=_severity(com.get("level") or "warning"),
            witness_hints={"message": com.get("message", "")},
            needs_M5_confirmation=False,
            m1_confidence=0.9,
        ))
    return flags
