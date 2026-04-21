"""Serialize M1 Flag records to the review-flags.jsonl append-only log.

Contract (read by Agent 3's M5 sandbox): one JSON per line with exactly the
fields below. Field names are fixed; extending the schema requires a
coordinated sandbox update.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterable

from m1_walker import Flag


# Canonical relative location under the plugin root.
DEFAULT_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "state",
    "review-flags.jsonl",
)


def _record(flag: Flag, ts: str) -> dict:
    return {
        "ts": ts,
        "file": flag.file,
        "line": flag.line,
        "function": flag.function,
        "rule_id": flag.rule_id,
        "flag_class": flag.flag_class,
        "severity": flag.severity,
        "witness_hints": flag.witness_hints,
        "needs_M5_confirmation": flag.needs_M5_confirmation,
        "m1_confidence": flag.m1_confidence,
    }


def emit(flags: Iterable[Flag], log_path: str = DEFAULT_LOG) -> int:
    """Append each flag as one JSON line. Creates parent dir if missing.
    Returns the number of lines written."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()
    n = 0
    with open(log_path, "a", encoding="utf-8") as fh:
        for flag in flags:
            fh.write(json.dumps(_record(flag, ts), ensure_ascii=False))
            fh.write("\n")
            n += 1
    return n
