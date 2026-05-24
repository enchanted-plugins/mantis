"""Ruff fast-path for M1 coverage.

When `ruff` is installed in the target project's environment, invoke it as
a subprocess, parse its JSON output, and convert correctness-bucket
findings into the same `Flag` records that `m1_walker.py` produces. When
ruff is absent, the adapter returns cleanly so callers fall back to the
stdlib walker.

Contract (brand invariants from CLAUDE.md):
    - Zero runtime deps on Lich's side. Ruff is optional in the target.
    - Security S-series rules NEVER map to M1. Hydra R3 owns CWEs. We
      hard-code a guard (prefix check) that refuses to emit an M1 flag
      for an S-series rule even if a registry bug accidentally listed one.
    - Advisory only. Subprocess crash, timeout, or malformed JSON -> log
      and return None / []; never raise to the CLI.
    - Only the `correctness_m1` bucket routes to M1 flags in Slice A.
      `idiom_m7`, `complexity_m7`, `naming_m7`, `testability_m7` belong
      to M7 (Slice B) and are not emitted here.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from m1_walker import Flag


# -------------------------------------------------------------------------
# Registry loading
# -------------------------------------------------------------------------

_REGISTRY_REL = os.path.join(
    "shared", "rules", "languages", "python.json"
)


def _repo_root() -> str:
    """Walk up from this file to the repo root (the dir containing `shared/`)."""
    here = os.path.dirname(os.path.abspath(__file__))
    # scripts -> lich-core -> plugins -> repo_root
    return os.path.dirname(os.path.dirname(os.path.dirname(here)))


def _severity_for(rule_id: str) -> str:
    """Heuristic severity bucket based on ruff rule prefix.

    F-series (pyflakes) and B-series (bugbear) are HIGH — real latent bugs.
    E-series (pycodestyle errors) and RUF are MED.
    UP / SIM / PLE-low / everything else falls to LOW.
    """
    if rule_id.startswith("F") or rule_id.startswith("B"):
        return "HIGH"
    if rule_id.startswith("E") or rule_id.startswith("RUF"):
        return "MED"
    if rule_id.startswith(("UP", "SIM", "PLE")):
        return "LOW"
    return "LOW"


def load_registry(path: Optional[str] = None) -> dict:
    """Read `shared/rules/languages/python.json` and return a flat map of
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
            # Skip wildcard-family provenance entries like "S1**".
            if "*" in rid:
                continue
            out[rid] = {
                "bucket": bucket,
                "severity": _severity_for(rid),
                "route": route,
            }
    return out


# -------------------------------------------------------------------------
# Ruff invocation
# -------------------------------------------------------------------------


def detect_ruff() -> Optional[str]:
    """Return the absolute path to `ruff` if on PATH, else None."""
    return shutil.which("ruff")


def run_ruff(
    file_path: str,
    ruff_path: str,
    timeout_s: int = 5,
) -> Optional[list[dict]]:
    """Invoke ruff on `file_path`, parse JSON, and return the finding list.

    Returns None on any failure (subprocess error, timeout, non-zero exit
    with no parseable JSON, JSONDecodeError). An empty list is a legitimate
    "no findings" result and is distinct from None.
    """
    try:
        proc = subprocess.run(
            [ruff_path, "check", "--output-format=json", "--no-fix", file_path],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps({"status": "ruff-timeout", "file": file_path,
                        "timeout_s": timeout_s}),
            file=sys.stderr,
        )
        return None
    except (OSError, FileNotFoundError) as exc:
        print(
            json.dumps({"status": "ruff-invocation-failed",
                        "file": file_path, "error": str(exc)}),
            file=sys.stderr,
        )
        return None

    # Ruff exits non-zero when findings are present — that's NOT an error.
    # We only care that stdout is valid JSON.
    stdout = proc.stdout or ""
    if not stdout.strip():
        # No findings and no JSON — treat as empty.
        return []
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError as exc:
        print(
            json.dumps({"status": "ruff-malformed-json",
                        "file": file_path, "error": str(exc)}),
            file=sys.stderr,
        )
        return None
    if not isinstance(parsed, list):
        print(
            json.dumps({"status": "ruff-unexpected-shape",
                        "file": file_path, "type": type(parsed).__name__}),
            file=sys.stderr,
        )
        return None
    return parsed


# -------------------------------------------------------------------------
# Finding -> Flag conversion
# -------------------------------------------------------------------------


_WITNESS_HINTS_BY_RULE: dict[str, dict] = {
    "F401": {"reason": "unused-import", "fix_class": "remove-import"},
    "F811": {"reason": "redefined-name", "fix_class": "rename-or-remove"},
    "F841": {"reason": "unused-local-binding", "fix_class": "remove-assignment"},
    "B006": {"reason": "mutable-default-argument",
             "fix_class": "replace-with-none-and-initialize-inside"},
    "B008": {"reason": "function-call-in-default",
             "fix_class": "replace-with-none-and-initialize-inside"},
    "B011": {"reason": "assert-False",
             "fix_class": "raise-assertion-error-instead"},
    "E501": {"reason": "line-too-long", "fix_class": "break-line"},
    "RUF005": {"reason": "collection-literal-concat",
               "fix_class": "use-unpacking"},
    "RUF010": {"reason": "explicit-f-string-conversion",
               "fix_class": "use-conversion-flag"},
    "PLE0100": {"reason": "yield-inside-async-iterator"},
    "PLE0101": {"reason": "explicit-return-in-init"},
    "PLE0116": {"reason": "continue-in-finally"},
}


def _build_function_spans(source: str) -> list[tuple[int, int, str]]:
    """Parse `source` and return a list of `(start_line, end_line, name)`
    tuples for every FunctionDef / AsyncFunctionDef.

    Returns [] on SyntaxError — callers then resolve the function as "<module>".
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start) or start
            spans.append((start, end, node.name))
    # Narrowest enclosing function wins; sort largest first so later writes
    # (inner functions) overwrite outer ones at the same line.
    spans.sort(key=lambda s: s[1] - s[0], reverse=True)
    return spans


def _resolve_function(spans: list[tuple[int, int, str]], line: int) -> str:
    """Return the narrowest function name enclosing `line`, or `<module>`."""
    best: Optional[tuple[int, int, str]] = None
    for start, end, name in spans:
        if start <= line <= end:
            if best is None or (end - start) < (best[1] - best[0]):
                best = (start, end, name)
    return best[2] if best else "<module>"


def _is_security_rule(rule_id: str) -> bool:
    """Hard-coded guard: any ID starting with `S` followed by digits is
    Hydra's lane. Never emit an M1 flag for these — even if the registry
    accidentally lists one."""
    if not rule_id or not rule_id.startswith("S"):
        return False
    rest = rule_id[1:]
    return len(rest) > 0 and rest[0].isdigit()


def findings_to_flags(
    findings: list[dict],
    registry: dict,
    source_file: str,
) -> list[Flag]:
    """Convert ruff findings to M1 Flag records.

    Only findings whose rule_id routes to M1 (`correctness_m1` bucket) are
    emitted. Security S-series rules are dropped unconditionally.

    Reads `source_file` once to resolve function names from line numbers.
    """
    # Read source for function-span resolution — non-fatal if it fails.
    try:
        with open(source_file, "r", encoding="utf-8") as fh:
            src = fh.read()
        spans = _build_function_spans(src)
    except (OSError, UnicodeDecodeError):
        spans = []

    flags: list[Flag] = []
    for f in findings:
        rule_id = f.get("code") or ""
        if not rule_id:
            continue
        if _is_security_rule(rule_id):
            # Hard guard — even if registry misclassified it.
            continue
        entry = registry.get(rule_id)
        if entry is None:
            continue  # unknown rule — ignore (not in mapped ~120)
        if entry["route"] != "m1":
            continue  # idiom / complexity / naming / testability -> M7 later

        # Extract line. Ruff emits `location: {row, column}`.
        location = f.get("location") or {}
        line = int(location.get("row") or 0)
        if line <= 0:
            continue

        function = _resolve_function(spans, line) if spans else "<module>"
        hints = dict(_WITNESS_HINTS_BY_RULE.get(rule_id, {}))
        # Always carry ruff's message verbatim for downstream context.
        msg = f.get("message")
        if msg:
            hints["ruff_message"] = msg
        hints["source"] = "ruff"

        flags.append(
            Flag(
                file=source_file,
                line=line,
                function=function,
                rule_id=rule_id,
                flag_class="ruff",
                severity=entry["severity"],
                witness_hints=hints,
                # Ruff findings are deterministic lints — sandbox confirmation
                # is not meaningful for most of them (unused-import cannot
                # "fail at runtime" in the M5 sense). Default off.
                needs_M5_confirmation=False,
                m1_confidence=0.95,
            )
        )
    return flags


# -------------------------------------------------------------------------
# Convenience one-shot
# -------------------------------------------------------------------------


def analyze_with_ruff(file_path: str) -> Optional[list[Flag]]:
    """Detect-invoke-convert pipeline in one call. Returns None if ruff
    is not installed, else a (possibly empty) list of M1 flags."""
    ruff = detect_ruff()
    if ruff is None:
        return None
    findings = run_ruff(file_path, ruff)
    if findings is None:
        return []  # ruff present but invocation failed; advisory fallback
    registry = load_registry()
    return findings_to_flags(findings, registry, file_path)
