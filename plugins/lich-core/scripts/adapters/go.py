"""Staticcheck fast-path for M1 Go coverage.

When `staticcheck` is installed on the target developer's box, invoke it
as a subprocess against a single `.go` file, parse its line-delimited JSON
output, and convert correctness-bucket findings (SA-series) into the same
`Flag` records `m1_walker.py` produces. When staticcheck is absent, the
adapter returns cleanly so callers can fall back.

Contract (brand invariants from CLAUDE.md):
    - Zero runtime deps on Lich's side. Staticcheck is optional in the
      target project's toolchain.
    - Security-framed rules (gosec G-series and the crypto SA1018 entry
      enumerated under `security_defer_to_hydra`) NEVER map to M1.
      Hydra R3 owns CWEs. We hard-code a guard that refuses to emit an
      M1 flag for a rule whose bucket is `security_defer_to_hydra`, even
      if a registry edit accidentally double-lists one.
    - Advisory only. Subprocess crash, timeout, or malformed JSON -> log
      to stderr as a single JSON object and return None / []; never raise.
    - Only the `correctness_m1` bucket routes to M1 flags in Slice A.
      `idiom_m7`, `complexity_m7`, `naming_m7`, `testability_m7` belong
      to M7 (Slice B) and are not emitted here.

Parse note: unlike ruff (which emits one JSON array on stdout), staticcheck
with `-f json` emits **line-delimited JSON** — one object per line, no
outer array, no comma separators. We parse each non-empty line
independently and skip malformed lines (they shouldn't happen, but the
contract is advisory fail-open).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

# Sibling-module import. The `scripts/` dir is injected onto sys.path by the
# dispatcher and by the test harness.
from m1_walker import Flag


LANG = "go"
FILE_EXTENSIONS = [".go"]


# -------------------------------------------------------------------------
# Registry loading
# -------------------------------------------------------------------------

_REGISTRY_REL = os.path.join("shared", "rules", "languages", "go.json")


def _repo_root() -> str:
    """Walk up from this file to the repo root (dir containing `shared/`).

    scripts/adapters -> scripts -> lich-core -> plugins -> repo_root.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(here)))
    )


def _severity_for(rule_id: str) -> str:
    """Heuristic severity bucket from staticcheck prefix.

    - SA1xxx (invalid args to stdlib), SA2xxx (concurrency) → HIGH (real
      runtime failures).
    - SA4xxx (ineffective code / dead stores) → MED.
    - SA5xxx (correctness, control-flow) → HIGH (panics, nil maps).
    - SA9xxx (misuse patterns) → HIGH.
    - ST1xxx (stylecheck) → LOW.
    - Everything else (S, U, QF) → LOW.
    """
    if rule_id.startswith("SA1") or rule_id.startswith("SA2"):
        return "HIGH"
    if rule_id.startswith("SA4"):
        return "MED"
    if rule_id.startswith("SA5") or rule_id.startswith("SA9"):
        return "HIGH"
    if rule_id.startswith("ST1"):
        return "LOW"
    return "LOW"


def load_registry(path: Optional[str] = None) -> dict:
    """Read `shared/rules/languages/go.json` and return a flat map of
    `{rule_id: {"bucket": <name>, "severity": <HIGH|MED|LOW>, "route": <m1|m7|defer>}}`.

    Security rules are included for provenance but tagged `route="defer"`
    so the guard in `findings_to_flags` can refuse them even if a caller
    treats them as in-scope.
    """
    if path is None:
        path = os.path.join(_repo_root(), _REGISTRY_REL)
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    out: dict = {}
    for bucket, body in data.get("categories", {}).items():
        if bucket == "correctness_m1":
            route = "m1"
        elif bucket == "security_defer_to_hydra":
            route = "defer"
        else:
            route = "m7"
        for rid in body.get("rule_ids", []):
            if "*" in rid:
                # Skip wildcard-family provenance entries.
                continue
            out[rid] = {
                "bucket": bucket,
                "severity": _severity_for(rid),
                "route": route,
            }
    return out


# -------------------------------------------------------------------------
# Staticcheck invocation
# -------------------------------------------------------------------------


def detect() -> Optional[str]:
    """Return the absolute path to `staticcheck` if on PATH, else None."""
    return shutil.which("staticcheck")


def run_staticcheck(
    file_path: str,
    bin_path: str,
    timeout_s: int = 5,
) -> Optional[list[dict]]:
    """Invoke staticcheck on `file_path`, parse line-delimited JSON, and
    return the finding list.

    Returns None on subprocess error, timeout, or JSON parse failure where
    *no* line was decodable. An empty list is a legitimate "no findings"
    result and is distinct from None. Individual malformed lines are
    skipped with a stderr note; a parse that yields at least one valid
    finding still returns the list.
    """
    try:
        proc = subprocess.run(
            [bin_path, "-f", "json", file_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps({"status": "staticcheck-timeout", "file": file_path,
                        "timeout_s": timeout_s}),
            file=sys.stderr,
        )
        return None
    except (OSError, FileNotFoundError) as exc:
        print(
            json.dumps({"status": "staticcheck-invocation-failed",
                        "file": file_path, "error": str(exc)}),
            file=sys.stderr,
        )
        return None

    # Staticcheck exits non-zero when findings are present — that's NOT an
    # error. We only care that stdout is parseable line-delimited JSON.
    stdout = proc.stdout or ""
    if not stdout.strip():
        return []

    findings: list[dict] = []
    any_malformed = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            any_malformed = True
            print(
                json.dumps({"status": "staticcheck-malformed-line",
                            "file": file_path, "error": str(exc),
                            "line_sample": line[:80]}),
                file=sys.stderr,
            )
            continue
        if isinstance(obj, dict):
            findings.append(obj)
        else:
            any_malformed = True
            print(
                json.dumps({"status": "staticcheck-unexpected-shape",
                            "file": file_path,
                            "type": type(obj).__name__}),
                file=sys.stderr,
            )

    # If we saw lines but decoded zero of them, surface that as None.
    if not findings and any_malformed:
        return None
    return findings


# -------------------------------------------------------------------------
# Finding -> Flag conversion
# -------------------------------------------------------------------------


_WITNESS_HINTS_BY_RULE: dict[str, dict] = {
    "SA1000": {"reason": "invalid-regexp-pattern",
               "fix_class": "fix-regex-literal"},
    "SA1001": {"reason": "invalid-template-syntax",
               "fix_class": "fix-template-literal"},
    "SA1012": {"reason": "nil-context-passed",
               "fix_class": "use-context-TODO-or-Background"},
    "SA1019": {"reason": "deprecated-api-use",
               "fix_class": "migrate-to-replacement-api"},
    "SA4006": {"reason": "value-never-read",
               "fix_class": "remove-assignment-or-use-value"},
    "SA5000": {"reason": "nil-map-write",
               "fix_class": "initialize-map-with-make"},
    "SA5001": {"reason": "defer-before-error-check",
               "fix_class": "move-defer-after-error-check"},
    "SA5011": {"reason": "nil-deref-after-check",
               "fix_class": "early-return-on-nil"},
    "SA9003": {"reason": "empty-branch",
               "fix_class": "remove-empty-branch-or-add-body"},
}


def _bucket_is_security(bucket: str) -> bool:
    """Hard-coded guard: any rule from `security_defer_to_hydra` is
    Hydra's lane. Never emit an M1 flag for these — even if a future
    registry edit accidentally routes one to M1."""
    return bucket == "security_defer_to_hydra"


def _extract_location(finding: dict) -> tuple[int, str]:
    """Pull `(line, message)` from staticcheck's finding shape.

    Staticcheck JSONL shape (stable as of 2024.1.x):
        {
            "code": "SA1000",
            "severity": "error",
            "location": {"file": "...", "line": 42, "column": 7},
            "end": {...},
            "message": "...",
        }
    Missing/invalid line → 0 (caller drops the finding).
    """
    loc = finding.get("location") or {}
    line = 0
    try:
        line = int(loc.get("line") or 0)
    except (TypeError, ValueError):
        line = 0
    msg = finding.get("message") or ""
    return line, msg


def findings_to_flags(
    findings: list[dict],
    registry: dict,
    source_file: str,
) -> list[Flag]:
    """Convert staticcheck findings to M1 Flag records.

    Only findings whose rule_id routes to M1 (`correctness_m1` bucket) are
    emitted. Security-bucket rules are dropped unconditionally. Unknown
    rule IDs (not in the registry) are also dropped — Lich deliberately
    under-covers in Phase 1 rather than guess at severity.
    """
    flags: list[Flag] = []
    for f in findings:
        rule_id = f.get("code") or ""
        if not rule_id:
            continue
        entry = registry.get(rule_id)
        if entry is None:
            continue  # unknown rule — ignore (not in mapped set)
        if _bucket_is_security(entry["bucket"]):
            # Hard guard — even if route was misconfigured.
            continue
        if entry["route"] != "m1":
            continue  # idiom / complexity / naming / testability → M7 later

        line, msg = _extract_location(f)
        if line <= 0:
            continue

        # Function resolution would require `go/parser` (not in stdlib); we
        # leave the function slot as "<file>" so downstream consumers can
        # still group flags by file. A future adapter upgrade may shell out
        # to `gopls` for symbol resolution, but that adds a second tool
        # dependency.
        function = "<file>"

        hints = dict(_WITNESS_HINTS_BY_RULE.get(rule_id, {}))
        if msg:
            hints["staticcheck_message"] = msg
        hints["source"] = "staticcheck"

        flags.append(
            Flag(
                file=source_file,
                line=line,
                function=function,
                rule_id=rule_id,
                flag_class="staticcheck",
                severity=entry["severity"],
                witness_hints=hints,
                # Staticcheck findings are deterministic; M5 confirmation is
                # not meaningful for most (e.g. SA1019 deprecated-use can't
                # "fail at runtime" in the M5 sense). Default off.
                needs_M5_confirmation=False,
                m1_confidence=0.95,
            )
        )
    return flags


# -------------------------------------------------------------------------
# Convenience one-shot
# -------------------------------------------------------------------------


def analyze(file_path: str) -> list[Flag]:
    """Detect → run → map pipeline. Never returns None, never raises.

    Contract mirrors other language adapters: caller gets a (possibly
    empty) list of M1 flags and can rely on no exceptions escaping.
    """
    binary = detect()
    if binary is None:
        return []
    findings = run_staticcheck(file_path, binary)
    if findings is None:
        # Advisory fallback — staticcheck present but invocation failed.
        return []
    try:
        registry = load_registry()
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps({"status": "go-registry-load-failed",
                        "error": str(exc)}),
            file=sys.stderr,
        )
        return []
    return findings_to_flags(findings, registry, file_path)
