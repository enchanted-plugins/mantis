"""Semgrep polyglot + framework adapter.

Semgrep's rule IDs are dotted paths like
`python.django.security.injection.formatted-sql-query`. We read the
framework registry at shared/rules/frameworks/semgrep-community.json and
map correctness rules to M1 flags.

Security guard: any rule whose dotted path contains `.security.`, `.auth.`,
`.crypto.`, `.injection`, `.xss`, `.ssrf`, `.traversal`, or `.insecure` is
NEVER mapped to M1 — Hydra R3 owns that lane. The guard runs regardless
of what bucket the registry places the rule in.

Offline fallback: `--config=auto` pulls from semgrep.dev over HTTP. When
`LICH_SEMGREP_OFFLINE=1` is set we skip auto config and use local rules
(Phase 2: shared/rules/frameworks/semgrep-local/ — not yet shipped; we
return [] honestly).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from m1_walker import Flag

from ._base import (
    REPO_ROOT,
    _log,
    detect_binary,
    is_security_bucket,
    run_subprocess,
)

LANG = "polyglot"
FILE_EXTENSIONS: list[str] = []  # invoked on any file the dispatcher allows

_SECURITY_MARKERS = (
    ".security.", ".auth.", ".crypto.", ".injection", ".xss",
    ".ssrf", ".traversal", ".insecure",
)


def _is_security_rule(rule_id: str) -> bool:
    lower = rule_id.lower()
    return any(marker in lower for marker in _SECURITY_MARKERS)


def detect() -> Optional[str]:
    return detect_binary("semgrep")


def _load_framework_registry() -> dict:
    path = REPO_ROOT / "shared" / "rules" / "frameworks" / "semgrep-community.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _log({"event": "semgrep-registry-load-failed", "error": str(e)})
        return {}
    result: dict[str, str] = {}
    for bucket_name, bucket in (data.get("categories") or {}).items():
        for rule_id in bucket.get("rule_ids", []):
            result[rule_id] = bucket_name
    return result


def _severity_from_semgrep(severity: str) -> str:
    return {"ERROR": "HIGH", "WARNING": "MED", "INFO": "LOW"}.get(severity, "MED")


def analyze(file_path: str, *, timeout_s: int = 30) -> list[Flag]:
    binary = detect()
    if not binary:
        return []
    offline = os.environ.get("LICH_SEMGREP_OFFLINE") == "1"
    if offline:
        _log({"event": "semgrep-offline-mode"})
        return []
    proc = run_subprocess(
        [binary, "--config=auto", "--json", "--quiet", "--timeout", str(timeout_s), file_path],
        timeout_s=timeout_s + 5,
    )
    if not proc or not proc.stdout:
        return []
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        _log({"event": "semgrep-json-parse-failed", "error": str(e)})
        return []
    registry = _load_framework_registry()
    flags: list[Flag] = []
    for result in data.get("results", []):
        rule_id = result.get("check_id") or ""
        # Hard security guard — runs BEFORE registry lookup
        if _is_security_rule(rule_id):
            continue
        bucket = registry.get(rule_id, "unmapped")
        if is_security_bucket(bucket) or bucket != "correctness_m1":
            continue
        start = (result.get("start") or {}).get("line", 0)
        extra = result.get("extra") or {}
        flags.append(Flag(
            file=file_path,
            line=int(start),
            function="<unknown>",
            rule_id=f"SEMGREP-{rule_id.split('.')[-1]}",
            flag_class=rule_id,
            severity=_severity_from_semgrep(extra.get("severity", "WARNING")),
            witness_hints={"message": extra.get("message", "")},
            needs_M5_confirmation=False,
            m1_confidence=0.8,
        ))
    return flags
