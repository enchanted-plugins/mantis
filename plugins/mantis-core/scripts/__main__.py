"""CLI entrypoint for the M1 walker.

Usage:
    python plugins/mantis-core/scripts/__main__.py <file_path>
    python -m scripts <file_path>          # if on PYTHONPATH

Two-substrate pipeline:
    1. If ruff is on PATH in the target env, invoke it, convert
       correctness-bucket findings to M1 Flag records.
    2. Always run the stdlib `ast` walker for the three PY-M1-* rules.
    3. Dedup by (file, line, rule_id); ruff's richer record wins on tie.

On parse failure (AST walker only), emits
`{"status": "substrate-parse-failed", "file": ...}` to stderr and exits 0 —
we never fabricate flags from broken source. Ruff output (if any) is still
kept since ruff tolerates many things ast.parse doesn't.
"""

from __future__ import annotations

import json
import os
import sys

# Ensure local imports (emit_flags, m1_walker, ruff_adapter) resolve
# whether run as a module or a direct script — the hyphen in `mantis-core`
# makes the parent package unimportable, so script-relative sys.path is
# the robust way in.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from m1_walker import analyze_path, Flag  # noqa: E402
from emit_flags import emit, DEFAULT_LOG  # noqa: E402
import ruff_adapter  # noqa: E402

try:
    from adapters import dispatch as _adapter_dispatch  # noqa: E402
except Exception:  # pragma: no cover — adapters package optional at load
    _adapter_dispatch = None

# Repo-root sys.path shim for shared/learnings.py (advisory Gauss log).
_SHARED = os.path.abspath(os.path.join(_HERE, "..", "..", "..", "shared"))
if os.path.isdir(_SHARED) and _SHARED not in sys.path:
    sys.path.insert(0, _SHARED)
try:
    import learnings as _learnings  # type: ignore  # noqa: E402
except Exception:  # pragma: no cover — advisory
    _learnings = None


def _summary(flags) -> dict:
    by_rule: dict[str, int] = {}
    for f in flags:
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    return {"total": len(flags), "by_rule": by_rule}


def _dedup(ruff_flags: list[Flag], ast_flags: list[Flag]) -> tuple[list[Flag], int]:
    """Merge ruff and ast flags; drop ast duplicates on exact
    `(file, line, rule_id)` match against any ruff flag. Returns
    `(merged, dropped_count)`.

    Only cross-substrate duplicates are collapsed — within-substrate
    behavior of both ruff and the ast walker is preserved verbatim so
    their individual outputs don't change when they run alone.
    """
    ruff_keys = {(f.file, f.line, f.rule_id) for f in ruff_flags}
    merged: list[Flag] = list(ruff_flags)  # ruff first; richer record wins
    dropped = 0
    for f in ast_flags:
        key = (f.file, f.line, f.rule_id)
        if key in ruff_keys:
            dropped += 1
            continue
        merged.append(f)
    return merged, dropped


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: m1_walker <file_path>", file=sys.stderr)
        return 2

    path = argv[1]

    # -- Non-Python files: dispatch to language adapter -------------------
    # .py files keep the existing ruff+ast substrate below.
    _ext = os.path.splitext(path)[1].lower()
    if _ext and _ext != ".py" and _adapter_dispatch is not None:
        adapter_flags: list[Flag] = []
        adapters_tried: list[str] = []
        for analyze in _adapter_dispatch(path):
            try:
                out = analyze(path) or []
                adapters_tried.append(analyze.__module__.rsplit(".", 1)[-1])
                adapter_flags.extend(out)
            except Exception as e:  # advisory — never raise from M1
                print(json.dumps({"status": "adapter-error",
                                   "adapter": analyze.__module__, "error": str(e)}),
                       file=sys.stderr)
        written = emit(adapter_flags)
        summary = _summary(adapter_flags)
        summary["written_to"] = DEFAULT_LOG
        summary["lines_written"] = written
        summary["substrate"] = "adapter:" + ",".join(adapters_tried) if adapters_tried else "adapter:none"
        print(json.dumps(summary), file=sys.stderr)
        return 0

    # -- Substrate 1: ruff fast-path (optional) ---------------------------
    ruff_flags: list[Flag] = []
    ruff_path = ruff_adapter.detect_ruff()
    substrate_tag = "ast-only"
    if ruff_path is not None:
        substrate_tag = "ruff+ast"
        findings = ruff_adapter.run_ruff(path, ruff_path)
        if findings:
            try:
                registry = ruff_adapter.load_registry()
                ruff_flags = ruff_adapter.findings_to_flags(
                    findings, registry, path
                )
            except (OSError, json.JSONDecodeError) as exc:
                # Registry missing or malformed — advisory fallback.
                print(
                    json.dumps({
                        "status": "ruff-registry-unreadable",
                        "error": str(exc),
                    }),
                    file=sys.stderr,
                )
                ruff_flags = []

    # -- Substrate 2: stdlib ast walker (always) --------------------------
    ast_flags: list[Flag] = []
    try:
        ast_flags = analyze_path(path)
    except SyntaxError as _parse_err:
        print(
            json.dumps({"status": "substrate-parse-failed", "file": path}),
            file=sys.stderr,
        )
        # Gauss Accumulation — parse failure is a version-drift signal
        # (or the source predates the current AST substrate).
        if _learnings is not None:
            try:
                _learnings.safe_emit(
                    plugin="mantis-core",
                    code="F14",
                    axis="substrate-parse-failed",
                    hypothesis=f"target {path} failed to parse",
                    outcome=str(_parse_err)[:500],
                    counter="verify substrate version",
                )
            except Exception:
                pass
        # Ruff can still have produced useful findings on pre-parse-failure
        # source, so we fall through to emit those rather than exit 0 empty.
    except FileNotFoundError:
        print(
            json.dumps({"status": "file-not-found", "file": path}),
            file=sys.stderr,
        )
        return 2

    merged, deduped = _dedup(ruff_flags, ast_flags)

    # Log substrate breakdown to stderr so the hook / reviewer can see it.
    print(
        json.dumps({
            "substrate": substrate_tag,
            "ruff_flags": len(ruff_flags),
            "ast_flags": len(ast_flags),
            "deduped": deduped,
            "merged": len(merged),
        }),
        file=sys.stderr,
    )

    written = emit(merged)
    summary = _summary(merged)
    summary["written_to"] = DEFAULT_LOG
    summary["lines_written"] = written
    summary["substrate"] = substrate_tag
    print(json.dumps(summary), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
