"""Clippy fast-path for M1 Rust coverage.

When `cargo` is on PATH (and `cargo clippy` is callable) on the target
developer's box, locate the crate root for the target `.rs` file, invoke
`cargo clippy --message-format=json`, parse the line-delimited output,
filter findings to the requested source file, and map `clippy::*`
correctness-bucket rules into `Flag` records that `m1_walker.py`
produces.

Contract (brand invariants from CLAUDE.md):
    - Zero runtime deps on Lich's side. Clippy is optional in the target
      project's toolchain.
    - Security-framed clippy lints (listed under `security_defer_to_hydra`
      in the rust.json registry) NEVER map to M1. Hydra R3 owns the CWE
      taxonomy. A hard `_bucket_is_security` guard refuses to emit an M1
      flag for those rules even if a future registry edit misroutes one.
    - Advisory only. Subprocess crash, timeout, missing crate root, or
      malformed JSON → log to stderr as a single JSON object and return
      None / []; never raise.
    - Only the `correctness_m1` bucket routes to M1 flags in Slice A.

Compile-integration caveat: clippy is **compile-integrated** — it runs
the Rust frontend over the whole crate. On projects with compile errors,
clippy emits compiler errors instead of lint findings and cannot produce
a useful M1 signal. In that case we return [] and log
`status=rust-compile-error-clippy-skipped` to stderr. That's honest; we
never pretend clippy produced a green result when it couldn't run.

Scope caveat: clippy runs **project-wide**. For v2 we invoke it from the
crate root and filter the JSONL stream to only the caller's file path.
Analyzing one file still pays the full crate-compile cost the first run;
subsequent runs benefit from incremental compilation.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from m1_walker import Flag


LANG = "rust"
FILE_EXTENSIONS = [".rs"]


# -------------------------------------------------------------------------
# Registry loading
# -------------------------------------------------------------------------

_REGISTRY_REL = os.path.join("shared", "rules", "languages", "rust.json")


def _repo_root() -> str:
    """Walk up to the repo root (the dir containing `shared/`).

    scripts/adapters -> scripts -> lich-core -> plugins -> repo_root.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(here)))
    )


def _severity_for(rule_id: str) -> str:
    """Heuristic severity by correctness-lint kind.

    - `clippy::unwrap_used`, `clippy::expect_used`, `clippy::panic`,
      `clippy::unreachable` → HIGH (explicit panic paths).
    - `clippy::indexing_slicing`, `clippy::out_of_bounds_indexing`,
      `clippy::integer_overflow` → HIGH (runtime-failure candidates).
    - `non_snake_case`, `non_camel_case_types` → LOW (naming).
    - Everything else defaults to MED.
    """
    high = {
        "clippy::unwrap_used",
        "clippy::expect_used",
        "clippy::panic",
        "clippy::unreachable",
        "clippy::indexing_slicing",
        "clippy::out_of_bounds_indexing",
        "clippy::integer_overflow",
    }
    low = {"non_snake_case", "non_camel_case_types"}
    if rule_id in high:
        return "HIGH"
    if rule_id in low:
        return "LOW"
    return "MED"


def load_registry(path: Optional[str] = None) -> dict:
    """Read `shared/rules/languages/rust.json` and return a flat map of
    `{rule_id: {"bucket": <name>, "severity": <HIGH|MED|LOW>, "route": <m1|m7|defer>}}`.
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
                continue
            out[rid] = {
                "bucket": bucket,
                "severity": _severity_for(rid),
                "route": route,
            }
    return out


# -------------------------------------------------------------------------
# Crate-root discovery
# -------------------------------------------------------------------------


def find_crate_root(file_path: str) -> Optional[Path]:
    """Walk up from `file_path` looking for a directory containing
    `Cargo.toml`. Return the path to that directory or None if we hit
    the filesystem root without finding one.

    A plain `.rs` file outside any crate cannot be clippy-analyzed;
    returning None lets the caller bail cleanly.
    """
    try:
        start = Path(file_path).resolve()
    except (OSError, RuntimeError):
        return None
    # If they passed a directory, start there; otherwise start at the parent.
    current = start if start.is_dir() else start.parent
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        candidate = current / "Cargo.toml"
        if candidate.is_file():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


# -------------------------------------------------------------------------
# Clippy invocation
# -------------------------------------------------------------------------


def detect() -> Optional[str]:
    """Return the absolute path to `cargo` if on PATH, else None.

    Clippy is always invoked via `cargo clippy`; it ships with stable
    Rust toolchains since ~2021. We detect cargo rather than clippy
    directly because that's the stable user-facing entry point.
    """
    return shutil.which("cargo")


def run_clippy(
    file_path: str,
    bin_path: str,
    timeout_s: int = 30,
) -> Optional[list[dict]]:
    """Invoke `cargo clippy --message-format=json` from the crate root of
    `file_path` and return the parsed JSONL stream filtered to messages
    relevant to that file.

    Budget is 30s (clippy compiles). Returns None if no crate root is
    found, if the subprocess times out, or if clippy errored out before
    any parseable message. Returns [] when clippy ran but produced no
    findings for the file, or when clippy produced compile errors (logged
    to stderr as `rust-compile-error-clippy-skipped`).
    """
    crate_root = find_crate_root(file_path)
    if crate_root is None:
        print(
            json.dumps({"status": "rust-no-crate-root",
                        "file": file_path}),
            file=sys.stderr,
        )
        return None

    try:
        proc = subprocess.run(
            [bin_path, "clippy", "--message-format=json",
             "--quiet", "--no-deps"],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            cwd=str(crate_root),
        )
    except subprocess.TimeoutExpired:
        print(
            json.dumps({"status": "clippy-timeout", "file": file_path,
                        "timeout_s": timeout_s,
                        "crate_root": str(crate_root)}),
            file=sys.stderr,
        )
        return None
    except (OSError, FileNotFoundError) as exc:
        print(
            json.dumps({"status": "clippy-invocation-failed",
                        "file": file_path, "error": str(exc)}),
            file=sys.stderr,
        )
        return None

    stdout = proc.stdout or ""
    if not stdout.strip():
        return []

    try:
        target_abs = str(Path(file_path).resolve())
    except (OSError, RuntimeError):
        target_abs = file_path

    findings: list[dict] = []
    saw_compile_error = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Individual malformed lines are skipped — cargo occasionally
            # emits non-JSON progress noise on some channels.
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("reason") != "compiler-message":
            continue
        message = obj.get("message") or {}
        level = message.get("level") or ""
        # Compile errors come through with level=error and no clippy code.
        code = (message.get("code") or {}).get("code") or ""
        if level == "error" and not code.startswith("clippy::"):
            saw_compile_error = True
            continue
        if not code.startswith("clippy::") and code not in (
            "non_snake_case", "non_camel_case_types"
        ):
            continue
        # Filter by target file: at least one span must resolve to it.
        spans = message.get("spans") or []
        if not _any_span_matches(spans, target_abs, file_path):
            continue
        findings.append(obj)

    if saw_compile_error and not findings:
        print(
            json.dumps({"status": "rust-compile-error-clippy-skipped",
                        "file": file_path,
                        "crate_root": str(crate_root)}),
            file=sys.stderr,
        )
    return findings


def _any_span_matches(
    spans: list,
    target_abs: str,
    target_raw: str,
) -> bool:
    """Return True if any span's `file_name` resolves to our target file.

    Clippy spans use forward-slash paths relative to the crate root (or
    absolute); we compare both the absolute-resolved form and the raw
    input to catch each shape.
    """
    for span in spans:
        if not isinstance(span, dict):
            continue
        fname = span.get("file_name") or ""
        if not fname:
            continue
        try:
            resolved = str(Path(fname).resolve())
        except (OSError, RuntimeError):
            resolved = fname
        if resolved == target_abs:
            return True
        if fname == target_raw:
            return True
        # Last-ditch suffix match for relative paths under the crate root.
        if target_abs.endswith(fname.replace("/", os.sep)):
            return True
    return False


# -------------------------------------------------------------------------
# Finding -> Flag conversion
# -------------------------------------------------------------------------


_WITNESS_HINTS_BY_RULE: dict[str, dict] = {
    "clippy::unwrap_used": {"reason": "unwrap-on-option-or-result",
                            "fix_class": "replace-with-match-or-?"},
    "clippy::expect_used": {"reason": "expect-on-option-or-result",
                            "fix_class": "replace-with-match-or-?"},
    "clippy::indexing_slicing": {"reason": "direct-indexing-can-panic",
                                 "fix_class": "use-get-or-check-bounds"},
    "clippy::integer_overflow": {"reason": "integer-overflow-path",
                                 "fix_class": "use-checked-or-saturating-op"},
    "clippy::out_of_bounds_indexing": {"reason": "out-of-bounds-index",
                                       "fix_class": "bounds-check-first"},
    "clippy::panic": {"reason": "explicit-panic",
                      "fix_class": "return-result-instead"},
    "clippy::unreachable": {"reason": "unreachable-reached-path",
                            "fix_class": "handle-case-or-return-result"},
}


def _bucket_is_security(bucket: str) -> bool:
    """Hard-coded guard: any rule from `security_defer_to_hydra` is
    Hydra's lane. Never emit an M1 flag for these."""
    return bucket == "security_defer_to_hydra"


def _extract_line(message: dict) -> int:
    """Pull the earliest primary span's line_start; fall back to any span."""
    spans = message.get("spans") or []
    primary_lines = [
        span.get("line_start")
        for span in spans
        if isinstance(span, dict) and span.get("is_primary")
    ]
    for v in primary_lines:
        try:
            line = int(v or 0)
        except (TypeError, ValueError):
            continue
        if line > 0:
            return line
    for span in spans:
        if not isinstance(span, dict):
            continue
        try:
            line = int(span.get("line_start") or 0)
        except (TypeError, ValueError):
            continue
        if line > 0:
            return line
    return 0


def findings_to_flags(
    findings: list[dict],
    registry: dict,
    source_file: str,
) -> list[Flag]:
    """Convert clippy compiler-message records to M1 Flag records.

    Only `correctness_m1` rules survive. Security-bucket rules are
    dropped unconditionally by `_bucket_is_security`. Unknown rule IDs
    are dropped silently (Lich prefers under-coverage to guessed
    severity).
    """
    flags: list[Flag] = []
    for f in findings:
        if not isinstance(f, dict):
            continue
        message = f.get("message") or {}
        code_obj = message.get("code") or {}
        rule_id = code_obj.get("code") or ""
        if not rule_id:
            continue
        entry = registry.get(rule_id)
        if entry is None:
            continue
        if _bucket_is_security(entry["bucket"]):
            continue
        if entry["route"] != "m1":
            continue

        line = _extract_line(message)
        if line <= 0:
            continue

        # Function resolution would need a Rust parser; clippy occasionally
        # fills in a span-adjacent label but the contract is loose. Leave
        # as "<file>" — same choice the go adapter makes.
        function = "<file>"

        hints = dict(_WITNESS_HINTS_BY_RULE.get(rule_id, {}))
        msg_text = message.get("message") or ""
        if msg_text:
            hints["clippy_message"] = msg_text
        hints["source"] = "clippy"

        flags.append(
            Flag(
                file=source_file,
                line=line,
                function=function,
                rule_id=rule_id,
                flag_class="clippy",
                severity=entry["severity"],
                witness_hints=hints,
                needs_M5_confirmation=False,
                m1_confidence=0.95,
            )
        )
    return flags


# -------------------------------------------------------------------------
# Convenience one-shot
# -------------------------------------------------------------------------


def analyze(file_path: str) -> list[Flag]:
    """Detect → find crate root → run → map pipeline. Never returns None,
    never raises. Returns [] when cargo is absent, the file sits outside
    any crate, clippy times out, or clippy was blocked by compile errors.
    """
    binary = detect()
    if binary is None:
        return []
    findings = run_clippy(file_path, binary)
    if findings is None:
        return []
    try:
        registry = load_registry()
    except (OSError, json.JSONDecodeError) as exc:
        print(
            json.dumps({"status": "rust-registry-load-failed",
                        "error": str(exc)}),
            file=sys.stderr,
        )
        return []
    return findings_to_flags(findings, registry, file_path)
