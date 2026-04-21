"""Shared adapter primitives.

Each language adapter loads its bucketed registry from shared/rules/, invokes
a subprocess linter, parses the output, and emits M1 Flag records for
correctness-bucket rule IDs only. Security rules never route to M1 (Reaper's
lane) — the guard below refuses even if a registry edit accidentally lists
one in a non-security bucket.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve()
# plugins/mantis-core/scripts/adapters/_base.py -> repo root (parents[4])
REPO_ROOT = _HERE.parents[4]


def registry_path(language: str) -> Path:
    return REPO_ROOT / "shared" / "rules" / "languages" / f"{language}.json"


def load_registry(language: str) -> dict:
    """Returns {rule_id: (bucket_name, severity_hint)}.

    bucket_name is one of: correctness_m1, idiom_m7, complexity_m7,
    naming_m7, testability_m7, security_defer_to_reaper.
    """
    path = registry_path(language)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        _log({"event": "registry-load-failed", "language": language, "error": str(e)})
        return {}
    result: dict[str, tuple[str, str]] = {}
    for bucket_name, bucket in (data.get("categories") or {}).items():
        for rule_id in bucket.get("rule_ids", []):
            if rule_id.endswith("*") or "**" in rule_id:
                continue  # wildcards expand at runtime, not here
            result[rule_id] = (bucket_name, _default_severity(bucket_name))
    return result


def _default_severity(bucket: str) -> str:
    # correctness bucket = HIGH baseline; individual adapters override for
    # sub-rule heuristics before emitting.
    return {
        "correctness_m1": "HIGH",
        "idiom_m7": "LOW",
        "complexity_m7": "MED",
        "naming_m7": "LOW",
        "testability_m7": "LOW",
        "security_defer_to_reaper": "HIGH",
    }.get(bucket, "MED")


def is_security_bucket(bucket: str) -> bool:
    return bucket == "security_defer_to_reaper"


def run_subprocess(cmd: list[str], *, timeout_s: int,
                    cwd: Optional[str] = None,
                    expect_zero_exit: bool = False) -> Optional[subprocess.CompletedProcess]:
    """Invokes a linter, captures stdout+stderr, enforces timeout.

    Returns None on any failure (timeout, binary missing, etc). Never raises.
    Advisory-only per CLAUDE.md — linter errors degrade to empty findings.
    """
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=cwd,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log({"event": "subprocess-timeout", "cmd": cmd[0], "timeout_s": timeout_s})
        return None
    except FileNotFoundError:
        _log({"event": "subprocess-missing-binary", "cmd": cmd[0]})
        return None
    except Exception as e:
        _log({"event": "subprocess-error", "cmd": cmd[0], "error": str(e)})
        return None
    if expect_zero_exit and proc.returncode != 0:
        _log({"event": "subprocess-nonzero", "cmd": cmd[0], "rc": proc.returncode,
              "stderr_head": (proc.stderr or "")[:200]})
        return None
    return proc


def _log(event: dict) -> None:
    try:
        sys.stderr.write(json.dumps(event) + "\n")
    except Exception:
        pass


def detect_binary(name: str, *extras: str) -> Optional[str]:
    for candidate in (name, *extras):
        p = shutil.which(candidate)
        if p:
            return p
    return None
