"""Lich M2 Dispatcher — route by language to the right M2 tier.

Honest dispatcher that mirrors M5's ``platform-unsupported`` pattern:

  - ``.py``                   → Tier 1: ``m2_structural_diff.diff``
                                (GumTree-lite, stdlib ``ast``).

  - C-like / Ruby / Shell     → Tier 2: ``m2_token_diff.diff``
    (see ``m2_token_diff``     (degraded substrate, honest label
    for exact extension set)    ``substrate="token-diff-fallback"``).

  - Anything else              → ``DiffResult(substrate=
                                "m2-unsupported-language", edits=[])``.
                                No silent zero-edit "green" pretend-run.

Phase 3 (not in v2) will add Tier 3: true AST diff via subprocess to
a host-installed parser (TS via ``tsc --noEmit``, Go via ``gofmt -r``,
Rust via ``cargo`` / ``syn`` worker). Gated on the tool being on PATH;
falls through to Tier 2 on absence. Deliberately deferred.

Zero external runtime deps: stdlib ``argparse``, ``json``, ``os``,
``pathlib`` only.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

import m2_structural_diff as tier1
import m2_token_diff as tier2
from m2_structural_diff import DiffResult


# -------------------------------------------------------------------------
# Extension → routing
# -------------------------------------------------------------------------


# Tier-1: real Python AST.
_TIER1_EXTS = frozenset({".py"})

# Tier-2: token-diff fallback. Maps extension → family hint accepted by
# m2_token_diff.diff. Lich-core owns this whitelist; new extensions
# graduate in here after the regex patterns are verified to not
# mis-parse representative fixtures.
_TIER2_EXT_TO_FAMILY: dict[str, str] = {
    # C-like (brace-delimited, function/class keywords)
    ".ts": "c-like",
    ".tsx": "c-like",
    ".js": "c-like",
    ".jsx": "c-like",
    ".mjs": "c-like",
    ".cjs": "c-like",
    ".go": "c-like",
    ".rs": "c-like",
    ".java": "c-like",
    ".cpp": "c-like",
    ".cc": "c-like",
    ".cxx": "c-like",
    ".c": "c-like",
    ".h": "c-like",
    ".hpp": "c-like",
    # Ruby-like (def/end, class/end, module/end)
    ".rb": "ruby-like",
    # Shell-like (brace-delimited, function keyword)
    ".sh": "shell-like",
    ".bash": "shell-like",
    ".zsh": "shell-like",
}


def _resolve_route(file_path_or_language: str) -> tuple[str, str | None]:
    """Return ``(tier, hint)`` where tier ∈ {"tier1","tier2","unsupported"}
    and ``hint`` is the family passed to Tier-2 (or None).

    Accepts either a path (``foo.ts``), a bare extension (``.ts`` / ``ts``),
    or a family name directly (``c-like``, ``ruby-like``, ``shell-like``).
    """
    raw = file_path_or_language.strip()

    # Family name passthrough (dispatcher used programmatically).
    if raw.lower() in {"c-like", "ruby-like", "shell-like"}:
        return ("tier2", raw.lower())
    if raw.lower() == "python":
        return ("tier1", None)

    # Treat as path — Path.suffix handles both "foo.ts" and ".ts".
    suffix = Path(raw).suffix.lower()
    if not suffix and not raw.startswith("."):
        # Bare extension like "ts".
        suffix = "." + raw.lower()
    elif raw.startswith(".") and not suffix:
        suffix = raw.lower()

    if suffix in _TIER1_EXTS:
        return ("tier1", None)
    if suffix in _TIER2_EXT_TO_FAMILY:
        return ("tier2", _TIER2_EXT_TO_FAMILY[suffix])
    return ("unsupported", None)


# -------------------------------------------------------------------------
# Top-level entry
# -------------------------------------------------------------------------


def diff_by_language(
    old_source: str,
    new_source: str,
    file_path_or_language: str,
    *,
    time_budget_s: float = 2.0,
    min_dice: float = 0.6,
) -> DiffResult:
    """Route ``(old, new)`` to the right M2 tier and return the result.

    On unknown extension, emits ``substrate="m2-unsupported-language"``
    with ``edits=[]`` — honest skip, no silent green.
    """
    tier, hint = _resolve_route(file_path_or_language)

    if tier == "tier1":
        return tier1.diff(
            old_source, new_source, time_budget_s=time_budget_s, min_dice=min_dice
        )
    if tier == "tier2":
        return tier2.diff(
            old_source, new_source, language_hint=hint or "c-like", min_dice=min_dice
        )

    return DiffResult(
        edits=[],
        stats={"reason": "unsupported-language", "input": file_path_or_language},
        substrate="m2-unsupported-language",
    )


# -------------------------------------------------------------------------
# CLI
# -------------------------------------------------------------------------


def _result_to_json(result: DiffResult) -> str:
    payload = {
        "substrate": result.substrate,
        "stats": result.stats,
        "edits": [asdict(e) for e in result.edits],
    }
    if result.substrate == "unified-diff-fallback":
        payload["unified_diff"] = result.unified_diff
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Lich M2 dispatcher — routes by file extension to Tier-1 AST "
        "(Python) or Tier-2 token-diff fallback (TS/Go/Rust/Java/C/Ruby/Shell)."
    )
    ap.add_argument("--old-file", required=True, help="Path to old source")
    ap.add_argument("--new-file", required=True, help="Path to new source")
    ap.add_argument(
        "--language",
        help="Override language (e.g. 'python', 'c-like', '.ts'). "
        "Default: derive from --new-file extension.",
    )
    ap.add_argument("--time-budget", type=float, default=2.0)
    ap.add_argument("--min-dice", type=float, default=0.6)
    args = ap.parse_args(argv)

    with open(args.old_file, "r", encoding="utf-8") as fh:
        old = fh.read()
    with open(args.new_file, "r", encoding="utf-8") as fh:
        new = fh.read()

    route_key = args.language if args.language else args.new_file
    result = diff_by_language(
        old, new, route_key,
        time_budget_s=args.time_budget, min_dice=args.min_dice,
    )

    print(_result_to_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
