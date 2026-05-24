"""RuboCop adapter for Ruby M1 coverage.

`rubocop --format json` emits a single JSON object with per-file offenses.
We extract offenses for the requested file and map cop IDs (e.g.,
`Lint/UnusedBlockArgument`, `Style/For`) to M1 via the registry.
"""

from __future__ import annotations

import json
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

LANG = "ruby"
FILE_EXTENSIONS = [".rb"]


def detect() -> Optional[str]:
    return detect_binary("rubocop")


def _severity(level: str) -> str:
    return {
        "fatal": "HIGH",
        "error": "HIGH",
        "warning": "MED",
        "convention": "LOW",
        "refactor": "LOW",
        "info": "LOW",
    }.get(level.lower(), "MED")


def analyze(file_path: str, *, timeout_s: int = 10) -> list[Flag]:
    binary = detect()
    if not binary:
        return []
    proc = run_subprocess([binary, "--format", "json", file_path], timeout_s=timeout_s)
    if not proc or not proc.stdout:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _log({"event": "rubocop-json-parse-failed", "error": str(e)})
        return []
    registry = load_registry(LANG)
    flags: list[Flag] = []
    for file_rec in data.get("files", []):
        for off in file_rec.get("offenses", []):
            cop = off.get("cop_name") or ""
            bucket, _ = registry.get(cop, ("unmapped", "MED"))
            if is_security_bucket(bucket) or bucket != "correctness_m1":
                continue
            loc = off.get("location") or {}
            flags.append(Flag(
                file=file_path,
                line=int(loc.get("line") or 0),
                function="<unknown>",
                rule_id=f"RUBY-{cop}",
                flag_class=cop.lower().replace("/", "-"),
                severity=_severity(off.get("severity") or "warning"),
                witness_hints={"message": off.get("message", "")},
                needs_M5_confirmation=False,
                m1_confidence=0.85,
            ))
    return flags
