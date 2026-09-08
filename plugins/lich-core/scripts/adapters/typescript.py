"""Biome fast-path for M1 TypeScript/JavaScript coverage.

Why this file exists
--------------------
The lich-typescript plugin shipped a rule registry (93 rules in
`shared/rules/languages/typescript.json`, plus a 107-rule biome map in the
plugin's own config) and a SKILL prompt, but NO executable adapter. Nothing in
the repository read either file: `adapters.dispatch(".ts")` returned only the
semgrep polyglot adapter, so a `.ts` file received no TypeScript analysis at
all while the marketplace described a "TypeScript / JavaScript language
adapter ... ~80 of biome's 423 rules mapped".

The registry was real; the code path was missing. This adapter supplies it,
mirroring `go.py` so both language lanes obey one contract.

Contract
--------
    - Zero runtime deps on Lich's side. Biome is optional in the target
      project's toolchain; when it is absent the lane reports UNAVAILABLE
      rather than an empty (and therefore falsely clean) result.
    - Security-framed rules are NOT emitted as M1 flags. As in go.py the
      deferral is CAPABILITY-based, not branding-based: the lane is only
      reported covered when a Hydra report proves coverage (see handoff.py).
    - Advisory only. Subprocess crash, timeout or malformed JSON is reported
      as DEGRADED coverage and never as zero findings; nothing raises.
    - Only the `correctness_m1` bucket routes to M1. idiom/complexity/naming/
      testability belong to M7 and are not emitted here.

Parse note: biome's `--reporter=json` emits ONE JSON document on stdout with a
`diagnostics` array (unlike staticcheck's line-delimited objects), so the
parsing differs from go.py even though the surrounding contract is identical.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from m1_walker import Flag

_SHARED = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))),
    "shared", "scripts")
if _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
import coverage as cov  # noqa: E402
import handoff  # noqa: E402


LANG = "typescript"
FILE_EXTENSIONS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"]

_REGISTRY_REL = os.path.join("shared", "rules", "languages", "typescript.json")


def _repo_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(here)))
    )


def _default_timeout_s() -> int:
    """Biome is fast (typically well under a second per file), but the budget
    is configurable and, as in go.py, expiry degrades coverage instead of
    silently emptying the result."""
    raw = os.environ.get("LICH_BIOME_TIMEOUT_S", "")
    try:
        val = int(raw)
        return val if val > 0 else 60
    except (TypeError, ValueError):
        return 60


def _severity_for(rule_id: str) -> str:
    """Biome groups rules by category prefix; correctness issues are the ones
    that actually fail at runtime."""
    rid = rule_id.lower()
    if rid.startswith("correctness/"):
        return "HIGH"
    if rid.startswith("suspicious/"):
        return "MED"
    return "LOW"


def detect() -> Optional[str]:
    """Return a runnable biome invocation, or None if biome is unavailable.

    Prefers a real binary on PATH; falls back to a locally installed
    node_modules copy. `npx` is deliberately NOT used as a fallback because it
    may try to download a package, which is a network side effect a review
    tool must not cause implicitly.
    """
    direct = shutil.which("biome")
    if direct:
        return direct
    local = os.path.join("node_modules", ".bin",
                         "biome.cmd" if os.name == "nt" else "biome")
    if os.path.isfile(local):
        return os.path.abspath(local)
    return None


def load_registry(path: Optional[str] = None) -> dict:
    """Read `shared/rules/languages/typescript.json` into
    `{rule_id: {bucket, severity, route}}`."""
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
        for rid in body.get("rule_ids", []) + body.get("biome_rules", []):
            if "*" in rid:
                continue
            out[rid] = {"bucket": bucket,
                        "severity": _severity_for(rid),
                        "route": route}
    return out


def run_biome(file_path: str, bin_path: str,
              timeout_s: Optional[int] = None) -> Optional[list[dict]]:
    """Invoke biome and return its diagnostics list.

    Returns None on subprocess error, timeout or unparseable output; an empty
    list is a legitimate "no findings" result and is distinct from None.
    """
    if timeout_s is None:
        timeout_s = _default_timeout_s()
    try:
        proc = subprocess.run(
            [bin_path, "lint", "--reporter=json", file_path],
            capture_output=True, text=True, timeout=timeout_s,
            # Analyzer output is UTF-8. Without an explicit encoding Python
            # decodes with the host locale (cp1252 on Windows) and raises
            # UnicodeDecodeError inside the subprocess reader thread, which
            # can silently truncate or lose diagnostics.
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"status": "biome-timeout", "file": file_path,
                          "timeout_s": timeout_s}), file=sys.stderr)
        return None
    except (OSError, FileNotFoundError) as exc:
        print(json.dumps({"status": "biome-invocation-failed",
                          "file": file_path, "error": str(exc)}),
              file=sys.stderr)
        return None

    stdout = (proc.stdout or "").strip()
    if not stdout:
        return []
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError as exc:
        print(json.dumps({"status": "biome-malformed-json",
                          "file": file_path, "error": str(exc)}),
              file=sys.stderr)
        return None
    if isinstance(doc, dict):
        diags = doc.get("diagnostics")
        return diags if isinstance(diags, list) else []
    if isinstance(doc, list):
        return doc
    return []


def _rule_id(diag: dict) -> str:
    """Biome puts the rule under `category`, e.g. 'lint/correctness/noVoid'."""
    cat = diag.get("category") or ""
    return cat[len("lint/"):] if cat.startswith("lint/") else cat


def _location(diag: dict) -> int:
    """Extract the 1-based line from a biome diagnostic.

    Verified against biome 2.5.12, which emits
    `location: {path, start: {line, column}, end: {...}}`. Older/other shapes
    (`span`, a bare `line`) are accepted as fallbacks so a format change
    degrades to 0 - which drops the flag - rather than raising.
    """
    loc = diag.get("location") or {}
    for candidate in (loc.get("start"), loc.get("span"), loc):
        if isinstance(candidate, dict) and candidate.get("line") is not None:
            try:
                return int(candidate["line"])
            except (TypeError, ValueError):
                return 0
    return 0


def _message(diag: dict) -> str:
    """Biome puts human text in `message`; `description` is often null."""
    for key in ("description", "message"):
        val = diag.get(key)
        if isinstance(val, str) and val.strip():
            return val
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, dict):
                    parts.append(str(item.get("content", "")))
                else:
                    parts.append(str(item))
            joined = " ".join(p for p in parts if p).strip()
            if joined:
                return joined
    return ""


def findings_to_flags(diagnostics: list[dict], registry: dict,
                      source_file: str) -> list[Flag]:
    """Convert biome diagnostics to M1 Flag records.

    Only `correctness_m1` routes to M1. Security-bucket rules are dropped
    unconditionally here; whether that lane is actually covered is decided by
    handoff.py and reported through coverage, not assumed.
    """
    flags: list[Flag] = []
    for diag in diagnostics:
        rid = _rule_id(diag)
        entry = registry.get(rid)
        if entry is None or entry["route"] != "m1":
            continue
        line = _location(diag)
        if line <= 0:
            continue
        msg = _message(diag)
        flags.append(Flag(
            file=source_file, line=line, function="<file>", rule_id=rid,
            flag_class="biome", severity=entry["severity"],
            witness_hints={"biome_message": str(msg)[:400], "source": "biome"},
            needs_M5_confirmation=False, m1_confidence=0.9,
        ))
    return flags


def analyze_with_coverage(file_path: str):
    """Detect -> run -> map, returning (flags, coverage_entries).

    Mirrors go.py: every way this pipeline yields zero flags maps to a
    distinct coverage status, so a missing or failing analyser is never
    mistaken for a clean file.
    """
    def entry(status, notes, truncated=False):
        return cov.CoverageEntry(
            cls="correctness", engine="biome", depth=cov.AST, status=status,
            shapes_supported=["biome correctness_m1 bucket"],
            shapes_unsupported=[
                "security/CWE classes (deferred; see handoff)",
                "rule IDs absent from the TypeScript registry",
                "idiom/complexity/naming/a11y (M7, not emitted here)",
            ],
            truncated=truncated, notes=notes)

    binary = detect()
    if binary is None:
        return [], [entry(
            cov.UNAVAILABLE,
            "biome is not installed; no TypeScript/JavaScript correctness "
            "analysis ran. This is NOT a clean result.")]

    diagnostics = run_biome(file_path, binary)
    if diagnostics is None:
        return [], [entry(
            cov.DEGRADED,
            "biome did not return parseable output (timeout, invocation "
            "failure, or malformed JSON); analysis incomplete")]

    try:
        registry = load_registry()
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "ts-registry-load-failed",
                          "error": str(exc)}), file=sys.stderr)
        return [], [entry(
            cov.DEGRADED,
            f"TypeScript rule registry unreadable ({exc}); biome ran but its "
            f"diagnostics could not be mapped")]

    flags = findings_to_flags(diagnostics, registry, file_path)

    covered, reason = handoff.may_suppress("injection", file_path)
    if covered:
        sec_status, sec_note = cov.COMPLETE, f"deferred to Hydra: {reason}"
    else:
        sec_status = cov.UNSUPPORTED
        sec_note = (f"security lane not emitted here and deferral is NOT "
                    f"evidence-backed: {reason}. This lane is UNCOVERED by "
                    f"both tools.")
    sec_entry = cov.CoverageEntry(
        cls="injection", engine="biome+handoff", depth=cov.PATTERN,
        status=sec_status, shapes_supported=[],
        shapes_unsupported=["all CWE classes when deferral is unverified"],
        notes=sec_note)

    return flags, [
        entry(cov.PARTIAL,
              f"biome completed; {len(diagnostics)} diagnostic(s), "
              f"{len(flags)} mapped to M1."),
        sec_entry,
    ]


def analyze(file_path: str) -> list[Flag]:
    """Back-compat entrypoint. An empty list from it is NOT evidence the file
    is clean; prefer `analyze_with_coverage`."""
    flags, _coverage = analyze_with_coverage(file_path)
    return flags
