"""M3 Yamaguchi Property-Graph Traversal — Joern adapter (Phase 2).

Wraps Joern's Code Property Graph (CPG) as an *optional* subprocess. When
`joern` is on PATH, run canonical CPG queries across supported source files
and emit M1-compatible `Flag` records so downstream consumers (M5 sandbox,
M7 rubric, M-verdict) cannot distinguish M1 vs. M3 flags by shape. When
Joern isn't installed, the adapter degrades honestly: `detect()` returns
None, `analyze(path)` returns an empty list, and the M1 stdlib walker
remains authoritative for static-suspicion coverage.

What Joern gives us that the M1 walker and ruff don't:
    - **Inter-procedural dataflow**: a `None` returned from function A
      flows into a `.attr` access in function B, across files.
    - **Type-resolved call graphs**: multi-language (Python, JS, Java, ...)
      semantic matching that single-file ASTs can't reach.
    - **CPG queries** (Scala script fragments) for semantic patterns
      — we ship three v1 queries and template the rest as we grow.

Contract (brand invariants from CLAUDE.md):
    - **Zero runtime deps on Mantis's side.** Joern is a JVM-backed,
      ~200MB install; it is never installed at build time, never required,
      and never called by Mantis's plugin-install hooks. If absent,
      everything downstream continues via M1.
    - **Advisory only.** Subprocess crash, timeout, or malformed JSON =>
      log and return empty; never raise to the CLI.
    - **Correctness scope — never security.** M3 flags dataflow-reachable
      *runtime failures* (null->deref, unbounded->iter, dataflow->div).
      Any query mentioning CWE / injection / taint-sink terminology is
      Reaper's lane. A hard-coded refusal pattern list guards against
      accidental overlap — see `_SECURITY_GUARD_TOKENS`.
    - **Flag shape identical to M1.** Downstream consumers bind to
      `m1_walker.Flag`; M3 emits the same dataclass. The only
      distinguishing signal is `flag_class="m3-cpg"` and
      `witness_hints["source"]="joern"`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Optional

from m1_walker import Flag


# -------------------------------------------------------------------------
# Module-level constants
# -------------------------------------------------------------------------

# v1 primary language. Joern supports many; Python is first because Mantis's
# M1/ruff coverage is Python-first and the dataflow complement lands here.
LANG = "python"

# File extensions for which we claim M3 detection in v1. Others fall through
# to M1 only. Joern itself supports C/C++/Obj-C/Kotlin/Go/Swift/PHP etc.,
# but we gate emission to the three we've validated query coverage on.
FILE_EXTENSIONS = [".py", ".js", ".java"]

# Default timeout for a single Joern query. Joern's cold-start can be slow
# because it spins a JVM; 60s is a generous-but-bounded cap. Timeouts return
# None (empty flag list at analyze()); they are never fatal.
DEFAULT_TIMEOUT_S = 60

# Joern script template. `{target}` is shell-substituted client-side via
# Python string formatting (NOT shell interpolation) before write-to-temp.
# The script:
#   1. importCode — builds/loads the CPG for the target file.
#   2. Runs the caller-provided query fragment.
#   3. Emits a JSON array to stdout for our parser.
# The `QUERY` placeholder is replaced by the canonical query bodies below.
_JOERN_SCRIPT_TEMPLATE = r"""// Mantis M3 — Joern query script (auto-generated)
// Target: {target}
// Rule:   {rule_id}
importCode(inputPath = "{target}", projectName = "mantis_m3")
val findings = {query}
println(findings.toJson)
"""

# -------------------------------------------------------------------------
# Security guard — refuse anything Reaper owns
# -------------------------------------------------------------------------

# If a query body or rule_id mentions any of these tokens (case-insensitive),
# the adapter refuses to emit and returns []. Reaper R3 owns CWE taxonomy;
# peer-classifying CWEs from Mantis breaks the severity source-of-truth
# contract. This is a belt-and-suspenders guard in addition to scoping the
# v1 query set to correctness.
_SECURITY_GUARD_TOKENS = frozenset({
    "cwe",           # any CWE-* rule id or description
    "injection",     # SQLi / XSS / command injection
    "sqli",
    "xss",
    "ssrf",
    "rce",
    "xxe",
    "taint",         # taint *classification* is Reaper's; M3 does dataflow
                     # tracking but never labels it "taint" in the flag.
    "sanitizer",
    "sink",          # taint-sink is a security term
    "payload",
    "exploit",
    "vuln",
    "owasp",
    "credential",
    "secret",
    "password",
})


def _query_touches_security(rule_id: str, query: str) -> bool:
    """Return True if `rule_id` or `query` contains any security token.

    Case-insensitive substring match. Intentionally aggressive: false-positive
    refusals are fine (fallback is M1); false-negatives would duplicate
    Reaper and break the contract.
    """
    blob = f"{rule_id}\n{query}".lower()
    return any(tok in blob for tok in _SECURITY_GUARD_TOKENS)


# -------------------------------------------------------------------------
# Canonical v1 queries
# -------------------------------------------------------------------------

# Each entry: rule_id -> {"query": str, "severity": str, "flag_class": str}.
# Queries are Scala/Joern CPG fragments. They are written once here; adapter
# authors MUST NOT inline strings at call sites (that bypasses the security
# guard, which scans this dict).
_M3_QUERIES: dict[str, dict] = {
    # M3-001 cross-fn-null-deref:
    #   Track literal `None` / `null` return values that flow into
    #   `.attr` access sites in caller functions. Covers Python, JS, Java
    #   via Joern's unified reachableBy dataflow operator.
    "M3-001": {
        "query": (
            'cpg.returns.code("None|null").reachableByFlows('
            'cpg.fieldAccess).map(f => Map('
            '"file" -> f.elements.last.file.name.headOption.getOrElse(""),'
            '"line" -> f.elements.last.lineNumber.getOrElse(0),'
            '"function" -> f.elements.last.method.name,'
            # Use "deref_expr" rather than any *sink* label — "sink" is
            # Reaper's security vocabulary; the guard below refuses queries
            # that mention it.
            '"deref_expr" -> f.elements.last.code'
            ')).toList'
        ),
        "severity": "HIGH",
        "flag_class": "m3-cpg",
        "reason": "cross-function-null-dereference",
    },
    # M3-002 unbounded-iteration:
    #   Find `for x in y:` / equivalent iteration sites where `y`'s size
    #   flows from a parameter or external input with no length check
    #   upstream. Flags runtime-resource exhaustion risk, not security.
    "M3-002": {
        "query": (
            'cpg.call.name("<operator>.iter|<operator>.forEach").where('
            '_.argument.reachableBy(cpg.parameter)).map(c => Map('
            '"file" -> c.file.name.headOption.getOrElse(""),'
            '"line" -> c.lineNumber.getOrElse(0),'
            '"function" -> c.method.name,'
            '"iter_target" -> c.argument.code.headOption.getOrElse("")'
            ')).toList'
        ),
        "severity": "MED",
        "flag_class": "m3-cpg",
        "reason": "unbounded-iteration-over-external-input",
    },
    # M3-003 tainted-div:
    #   Division sites `a/b` where `b` has a dataflow path from a
    #   parameter / external input — a runtime-failure candidate for
    #   div-by-zero under adversarial inputs. We do NOT label this a
    #   taint-security finding; it is a correctness flag that M5's
    #   sandbox can confirm with a concrete `b=0` witness.
    "M3-003": {
        "query": (
            'cpg.call.name("<operator>.division").where('
            '_.argument(2).reachableBy(cpg.parameter)).map(c => Map('
            '"file" -> c.file.name.headOption.getOrElse(""),'
            '"line" -> c.lineNumber.getOrElse(0),'
            '"function" -> c.method.name,'
            '"denom_expr" -> c.argument(2).code.headOption.getOrElse("")'
            ')).toList'
        ),
        "severity": "HIGH",
        "flag_class": "m3-cpg",
        "reason": "dataflow-reachable-division-denominator",
    },
}


# -------------------------------------------------------------------------
# Detection
# -------------------------------------------------------------------------


def detect() -> Optional[str]:
    """Return the absolute path to the Joern CLI if installed, else None.

    Checks `joern` first, then `joern-cli` (the distribution name on some
    package managers). Advisory: a positive detection does not guarantee
    a usable CPG runtime — actual invocation in `_run_joern_query` is
    still wrapped in a try/except with a timeout.
    """
    return shutil.which("joern") or shutil.which("joern-cli")


# -------------------------------------------------------------------------
# Joern invocation
# -------------------------------------------------------------------------


def _render_script(file_path: str, rule_id: str, query: str) -> str:
    """Build the Scala script body fed to `joern --script`.

    The target path is embedded literally. We do NOT shell-escape because
    the script is written to a temp file and invoked via subprocess with
    an argument list (no shell). Backslashes in Windows paths are forward-
    slashed for Joern's path parser which prefers POSIX form.
    """
    safe_target = file_path.replace("\\", "/")
    return _JOERN_SCRIPT_TEMPLATE.format(
        target=safe_target, rule_id=rule_id, query=query
    )


def _run_joern_query(
    file_path: str,
    rule_id: str,
    query: str,
    joern_path: Optional[str] = None,
    timeout_s: int = DEFAULT_TIMEOUT_S,
) -> Optional[dict]:
    """Invoke `joern --script <tmp>` for a single rule query.

    Returns the parsed JSON payload (expected: `list[dict]`) on success, or
    None on any failure (not installed, subprocess error, timeout,
    malformed JSON, or security-guard refusal). An empty list `[]` is a
    *legitimate* "no findings" result and is returned as-is — distinct
    from None.
    """
    if _query_touches_security(rule_id, query):
        # Belt-and-suspenders. The _M3_QUERIES dict is audited; this guard
        # catches future edits that drift into Reaper's lane.
        print(
            json.dumps({
                "status": "m3-security-refusal",
                "rule_id": rule_id,
                "reason": "query references CWE/taint-sink vocabulary",
            }),
            file=sys.stderr,
        )
        return None

    if joern_path is None:
        joern_path = detect()
    if joern_path is None:
        return None

    script = _render_script(file_path, rule_id, query)
    tmp_path: Optional[str] = None
    try:
        # Write the script to a temp file; Joern's `--script` takes a path.
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sc", delete=False, encoding="utf-8"
        ) as fh:
            fh.write(script)
            tmp_path = fh.name

        try:
            proc = subprocess.run(
                [joern_path, "--script", tmp_path],
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            print(
                json.dumps({
                    "status": "joern-timeout",
                    "file": file_path,
                    "rule_id": rule_id,
                    "timeout_s": timeout_s,
                }),
                file=sys.stderr,
            )
            return None
        except (OSError, FileNotFoundError) as exc:
            print(
                json.dumps({
                    "status": "joern-invocation-failed",
                    "file": file_path,
                    "rule_id": rule_id,
                    "error": str(exc),
                }),
                file=sys.stderr,
            )
            return None

        stdout = proc.stdout or ""
        if not stdout.strip():
            return {"findings": []}
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            print(
                json.dumps({
                    "status": "joern-malformed-json",
                    "file": file_path,
                    "rule_id": rule_id,
                    "error": str(exc),
                }),
                file=sys.stderr,
            )
            return None
        if not isinstance(parsed, list):
            print(
                json.dumps({
                    "status": "joern-unexpected-shape",
                    "file": file_path,
                    "rule_id": rule_id,
                    "type": type(parsed).__name__,
                }),
                file=sys.stderr,
            )
            return None
        return {"findings": parsed}
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# -------------------------------------------------------------------------
# Finding -> Flag
# -------------------------------------------------------------------------


def _finding_to_flag(
    file_path: str,
    rule_id: str,
    rule_meta: dict,
    finding: dict,
) -> Optional[Flag]:
    """Convert one Joern finding dict to a Flag. Returns None if required
    fields are missing. Shape-identical to M1 Flag (see m1_walker.Flag)."""
    line_raw = finding.get("line")
    try:
        line = int(line_raw) if line_raw is not None else 0
    except (TypeError, ValueError):
        line = 0
    if line <= 0:
        return None

    function = finding.get("function") or "<module>"
    file_from_joern = finding.get("file") or file_path

    hints: dict = {
        "source": "joern",
        "reason": rule_meta["reason"],
        "engine": "M3",
    }
    # Pass through Joern-provided context verbatim under namespaced keys.
    for k in ("deref_expr", "iter_target", "denom_expr"):
        if finding.get(k):
            hints[k] = finding[k]
    # Boundary-value hints for M5 sandbox witness synthesis.
    if rule_id == "M3-001":
        hints["boundary_values"] = [None]
    elif rule_id == "M3-003":
        hints["boundary_values"] = [0]
    # M3-002 has no scalar boundary; witness synthesis would build a
    # large collection. Leave absent — sandbox uses the default strategy.

    return Flag(
        file=file_from_joern,
        line=line,
        function=function,
        rule_id=rule_id,
        flag_class=rule_meta["flag_class"],
        severity=rule_meta["severity"],
        witness_hints=hints,
        needs_M5_confirmation=True,
        m1_confidence=0.8,   # slightly below M1's 0.9: dataflow is
                             # inherently over-approximate vs. single-
                             # function abstract interpretation.
    )


def _findings_to_flags(
    file_path: str,
    rule_id: str,
    rule_meta: dict,
    findings: list[dict],
) -> list[Flag]:
    out: list[Flag] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        flag = _finding_to_flag(file_path, rule_id, rule_meta, f)
        if flag is not None:
            out.append(flag)
    return out


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------


def _ext_supported(file_path: str) -> bool:
    _, ext = os.path.splitext(file_path)
    return ext.lower() in FILE_EXTENSIONS


def analyze(file_path: str, timeout_s: int = DEFAULT_TIMEOUT_S) -> list[Flag]:
    """Run all canonical M3 queries against `file_path` and return Flags.

    Degrades to `[]` when:
      - Joern is not installed (detect() is None)
      - file extension is outside FILE_EXTENSIONS
      - any/all queries time out, error, or refuse via security guard

    Never raises. Never overlaps Reaper. Flag shape is identical to M1.
    """
    if not _ext_supported(file_path):
        return []
    joern_path = detect()
    if joern_path is None:
        return []

    all_flags: list[Flag] = []
    for rule_id, rule_meta in _M3_QUERIES.items():
        result = _run_joern_query(
            file_path=file_path,
            rule_id=rule_id,
            query=rule_meta["query"],
            joern_path=joern_path,
            timeout_s=timeout_s,
        )
        if result is None:
            # Advisory fallback — skip this rule, continue others.
            continue
        findings = result.get("findings") or []
        if not isinstance(findings, list):
            continue
        all_flags.extend(_findings_to_flags(
            file_path, rule_id, rule_meta, findings
        ))
    return all_flags
