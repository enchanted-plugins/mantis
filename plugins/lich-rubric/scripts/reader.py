"""Reader for plugins/lich-rubric/state/kappa-log.jsonl.

Exposes the minimum surface compose.py (in lich-verdict) needs to pull
per-file M7 scores without importing ingest-side code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[2]
_DEFAULT_LOG = _REPO_ROOT / "plugins" / "lich-rubric" / "state" / "kappa-log.jsonl"


def _normalize(p: str) -> str:
    return p.replace("\\", "/")


def _iter_records(log_path: Path):
    if not log_path.exists():
        return
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                # Skip malformed lines rather than tainting the reader path.
                continue


def latest_for(file: str, log_path: Optional[Path] = None) -> Optional[dict]:
    """Return the most-recent record for `file` (by ts), or None.

    Path normalization: forward slashes on both sides so Windows-style
    inputs match records written with forward slashes.
    """
    path = log_path or _DEFAULT_LOG
    target = _normalize(file)
    latest: Optional[dict] = None
    for rec in _iter_records(path):
        if _normalize(rec.get("file", "")) != target:
            continue
        if latest is None or rec.get("ts", "") > latest.get("ts", ""):
            latest = rec
    return latest


def all_files_with_scores(log_path: Optional[Path] = None) -> set:
    """Return the set of normalized file paths present in the log."""
    path = log_path or _DEFAULT_LOG
    out: set = set()
    for rec in _iter_records(path):
        f = rec.get("file")
        if f:
            out.add(_normalize(f))
    return out
