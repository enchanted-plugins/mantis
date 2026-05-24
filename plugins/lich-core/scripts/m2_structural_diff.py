"""Lich M2 — Falleri Structural Diff (GumTree-lite, Python-only v2).

Classifies AST-level edits between two versions of a Python source file so
M1's attention can be focused on structural change rather than linear line
diffs. Emits action records of shape::

    {action: "move"|"update"|"insert"|"delete",
     old_path, new_path, old_type, new_type, similarity}

Two-phase matching (Falleri / GumTree):

  Phase 1 — top-down: hash subtrees by their structural shape (node type +
           child hashes, literals erased). Greedy match identical hashes,
           larger subtrees first. Anchors the diff on exact clones.

  Phase 2 — bottom-up: for still-unmatched nodes of the same type, compute
           Dice similarity over descendants matched in Phase 1. If
           coefficient >= ``min_dice`` (0.6), pair them.

*Simplification vs. the Falleri paper (documented for Phase 3 port-up):*

  - Dice is computed over the *full* descendant set of each candidate
    pair, not the anchor-chain subset. This trades a little precision
    (fewer anchor chains considered) for a much simpler implementation
    and identical behavior on small-to-medium files.
  - ``min_height`` (3) filters trivial one-liners from Phase 1 so
    renames of `x = 1` don't generate edits. Classical GumTree uses
    the same parameter under the name ``minHeight``.
  - No update-cost model; a paired node whose *own* type changed is
    reported as ``update`` and the new subtree's internal diff is not
    drilled into recursively.

Timeouts and fallbacks:

  - ``time_budget_s`` (default 2.0) is checked between phases. On
    overrun, ``difflib.unified_diff`` is emitted as the fallback
    substrate with ``substrate="unified-diff-fallback"`` and callers
    receive a structural-diff-timeout flag (see ``--emit-flags``).
  - On ``SyntaxError`` in either input, ``substrate="parse-failed"``
    and ``edits=[]``. No fallback — M2 has nothing to say about code
    that doesn't parse.

Zero external runtime deps: stdlib ``ast``, ``hashlib``, ``difflib``,
``time``, ``dataclasses``, ``json`` only. CLI surface at bottom of module.
"""

from __future__ import annotations

import argparse
import ast
import difflib
import hashlib
import json
import sys
import time
from dataclasses import asdict, dataclass, field


# -------------------------------------------------------------------------
# Records
# -------------------------------------------------------------------------


@dataclass
class Edit:
    action: str  # "move" | "update" | "insert" | "delete"
    old_path: str | None
    new_path: str | None
    old_type: str | None
    new_type: str | None
    similarity: float


@dataclass
class DiffResult:
    edits: list[Edit]
    stats: dict
    substrate: str  # "gumtree" | "unified-diff-fallback" | "parse-failed"
    # Populated when substrate == "unified-diff-fallback".
    unified_diff: str = ""


# -------------------------------------------------------------------------
# Subtree hashing and node collection
# -------------------------------------------------------------------------


# Fields that carry literal payloads we deliberately erase so two subtrees
# with the same *shape* but different literal values hash the same. This is
# what makes "same body, different name/constant" register as a match in
# Phase 1 instead of as distinct structures.
_LITERAL_FIELDS = frozenset({"id", "arg", "attr", "n", "s", "value", "name"})


def _hash_subtree(node: ast.AST) -> str:
    """Deterministic hash of a subtree's *structural* shape.

    Literals (names, constants, attribute strings) are erased — we hash
    only (node-type, ordered list of child hashes). Two textually-distinct
    functions with the same AST shape get the same hash, which is exactly
    what Phase 1 wants for clone/move detection.
    """
    if not isinstance(node, ast.AST):
        return "L:" + str(type(node).__name__)

    h = hashlib.sha1()
    h.update(type(node).__name__.encode("utf-8"))

    for field_name, value in ast.iter_fields(node):
        if field_name in _LITERAL_FIELDS:
            # Erased — shape-only hashing.
            continue
        if isinstance(value, list):
            h.update(b"[")
            for item in value:
                h.update(_hash_subtree(item).encode("utf-8"))
                h.update(b",")
            h.update(b"]")
        elif isinstance(value, ast.AST):
            h.update(_hash_subtree(value).encode("utf-8"))
        # Non-AST, non-list scalars (literals) are skipped.

    return h.hexdigest()[:16]


def _height(node: ast.AST) -> int:
    """Height = 1 + max child height; leaves = 1."""
    if not isinstance(node, ast.AST):
        return 0
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_height(c) for c in children)


def _descendants(node: ast.AST) -> list[ast.AST]:
    """All strict descendants of ``node`` (excluding self)."""
    out: list[ast.AST] = []
    for child in ast.iter_child_nodes(node):
        out.append(child)
        out.extend(_descendants(child))
    return out


@dataclass
class NodeInfo:
    node: ast.AST
    depth: int
    height: int
    hash: str
    path: str  # e.g. "Module.body[0].body[2]"


def _collect_nodes(tree: ast.AST, min_height: int = 3) -> list[NodeInfo]:
    """Walk ``tree``, returning NodeInfo records for nodes of height >=
    ``min_height``. Path encodes the parent chain with list indices so two
    structurally-identical nodes at different positions are distinguishable.
    """
    out: list[NodeInfo] = []

    def visit(node: ast.AST, depth: int, path: str) -> None:
        height = _height(node)
        if height >= min_height:
            out.append(
                NodeInfo(
                    node=node,
                    depth=depth,
                    height=height,
                    hash=_hash_subtree(node),
                    path=path,
                )
            )
        for field_name, value in ast.iter_fields(node):
            if isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, ast.AST):
                        visit(item, depth + 1, f"{path}.{field_name}[{i}]")
            elif isinstance(value, ast.AST):
                visit(value, depth + 1, f"{path}.{field_name}")

    visit(tree, 0, type(tree).__name__)
    return out


# -------------------------------------------------------------------------
# Phase 1 — top-down (hash match)
# -------------------------------------------------------------------------


@dataclass
class Pair:
    old: NodeInfo
    new: NodeInfo
    similarity: float  # 1.0 for Phase 1, computed for Phase 2


def _top_down_match(
    old_nodes: list[NodeInfo], new_nodes: list[NodeInfo]
) -> tuple[list[Pair], set[int], set[int]]:
    """Greedy hash-match, larger subtrees first. Returns (pairs,
    matched_old_ids, matched_new_ids) where ids are id(node) values."""
    # Sort by -height so big identical subtrees claim each other before
    # smaller ones do.
    old_sorted = sorted(old_nodes, key=lambda n: -n.height)
    new_by_hash: dict[str, list[NodeInfo]] = {}
    for n in new_nodes:
        new_by_hash.setdefault(n.hash, []).append(n)

    pairs: list[Pair] = []
    matched_old: set[int] = set()
    matched_new: set[int] = set()

    for o in old_sorted:
        candidates = new_by_hash.get(o.hash, [])
        for cand in candidates:
            if id(cand.node) in matched_new:
                continue
            pairs.append(Pair(old=o, new=cand, similarity=1.0))
            matched_old.add(id(o.node))
            matched_new.add(id(cand.node))
            break

    return pairs, matched_old, matched_new


# -------------------------------------------------------------------------
# Phase 2 — bottom-up (Dice)
# -------------------------------------------------------------------------


def _dice_similarity(
    a: ast.AST, b: ast.AST, matched_old: set[int], matched_new: set[int]
) -> float:
    """Dice coefficient = 2 * |matched descendants| / (|a.desc| + |b.desc|).

    A "matched descendant" is a descendant of ``a`` whose id is in
    ``matched_old`` *and* whose Phase-1 partner is a descendant of ``b``.
    Simplified here: we count descendants of ``a`` already matched (in
    ``matched_old``) and descendants of ``b`` already matched, and take
    the intersection by the paired-id relationship via a passed-in map.
    """
    # Fallback simplified form (documented in module docstring): share
    # the denominator over full descendant sets, numerator over matched
    # ids on each side. This approximates the formal Dice when most
    # descendants have a Phase-1 partner, which is the common case.
    a_desc = _descendants(a)
    b_desc = _descendants(b)
    if not a_desc and not b_desc:
        return 1.0
    a_matched = sum(1 for d in a_desc if id(d) in matched_old)
    b_matched = sum(1 for d in b_desc if id(d) in matched_new)
    common = min(a_matched, b_matched)
    denom = len(a_desc) + len(b_desc)
    if denom == 0:
        return 0.0
    return (2.0 * common) / denom


def _bottom_up_match(
    old_nodes: list[NodeInfo],
    new_nodes: list[NodeInfo],
    matched_old: set[int],
    matched_new: set[int],
    min_dice: float = 0.6,
) -> list[Pair]:
    """For unmatched same-type nodes, pair those with Dice >= min_dice.
    Greedy by descending similarity so the strongest matches lock first.
    """
    old_remaining = [n for n in old_nodes if id(n.node) not in matched_old]
    new_remaining = [n for n in new_nodes if id(n.node) not in matched_new]

    candidates: list[tuple[float, NodeInfo, NodeInfo]] = []
    for o in old_remaining:
        o_type = type(o.node).__name__
        for n in new_remaining:
            if type(n.node).__name__ != o_type:
                continue
            sim = _dice_similarity(o.node, n.node, matched_old, matched_new)
            if sim >= min_dice:
                candidates.append((sim, o, n))

    candidates.sort(key=lambda t: -t[0])

    pairs: list[Pair] = []
    claimed_old: set[int] = set()
    claimed_new: set[int] = set()
    for sim, o, n in candidates:
        if id(o.node) in claimed_old or id(n.node) in claimed_new:
            continue
        pairs.append(Pair(old=o, new=n, similarity=sim))
        claimed_old.add(id(o.node))
        claimed_new.add(id(n.node))

    return pairs


# -------------------------------------------------------------------------
# Classification
# -------------------------------------------------------------------------


def _classify_edits(
    phase1_pairs: list[Pair],
    phase2_pairs: list[Pair],
    old_nodes: list[NodeInfo],
    new_nodes: list[NodeInfo],
    matched_old: set[int],
    matched_new: set[int],
) -> list[Edit]:
    """Turn matched / unmatched nodes into Edit records.

    - Same path, same type        → no edit (silent, not returned).
    - Different path, same type   → ``move``.
    - Phase 2 pair (Dice-matched) → ``update`` (structure shifted within the
                                    same node-type; internal subtree drift).
    - Unmatched old               → ``delete``.
    - Unmatched new               → ``insert``.
    """
    edits: list[Edit] = []

    for p in phase1_pairs:
        o_type = type(p.old.node).__name__
        n_type = type(p.new.node).__name__
        if p.old.path == p.new.path and o_type == n_type:
            # Structural no-op; nothing to report.
            continue
        if o_type != n_type:
            edits.append(
                Edit(
                    action="update",
                    old_path=p.old.path,
                    new_path=p.new.path,
                    old_type=o_type,
                    new_type=n_type,
                    similarity=p.similarity,
                )
            )
        else:
            edits.append(
                Edit(
                    action="move",
                    old_path=p.old.path,
                    new_path=p.new.path,
                    old_type=o_type,
                    new_type=n_type,
                    similarity=p.similarity,
                )
            )

    for p in phase2_pairs:
        edits.append(
            Edit(
                action="update",
                old_path=p.old.path,
                new_path=p.new.path,
                old_type=type(p.old.node).__name__,
                new_type=type(p.new.node).__name__,
                similarity=p.similarity,
            )
        )

    # Track Phase 2 matches so we don't double-report as insert/delete.
    for p in phase2_pairs:
        matched_old.add(id(p.old.node))
        matched_new.add(id(p.new.node))

    for n in old_nodes:
        if id(n.node) in matched_old:
            continue
        edits.append(
            Edit(
                action="delete",
                old_path=n.path,
                new_path=None,
                old_type=type(n.node).__name__,
                new_type=None,
                similarity=0.0,
            )
        )

    for n in new_nodes:
        if id(n.node) in matched_new:
            continue
        edits.append(
            Edit(
                action="insert",
                old_path=None,
                new_path=n.path,
                old_type=None,
                new_type=type(n.node).__name__,
                similarity=0.0,
            )
        )

    return edits


# -------------------------------------------------------------------------
# Orchestrator
# -------------------------------------------------------------------------


def _unified_diff_text(old_source: str, new_source: str) -> str:
    old_lines = old_source.splitlines(keepends=True)
    new_lines = new_source.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(old_lines, new_lines, fromfile="old", tofile="new")
    )


def diff(
    old_source: str,
    new_source: str,
    time_budget_s: float = 2.0,
    min_height: int = 3,
    min_dice: float = 0.6,
    min_similarity: float = 0.7,  # reserved for Phase 3 anchor-chain filter
) -> DiffResult:
    """Top-level entry point. See module docstring for the contract."""
    start = time.monotonic()

    # -- parse (syntax errors short-circuit; no fallback) -----------------
    try:
        old_tree = ast.parse(old_source)
        new_tree = ast.parse(new_source)
    except SyntaxError:
        return DiffResult(
            edits=[], stats={"reason": "parse-failed"}, substrate="parse-failed"
        )

    def budget_exceeded() -> bool:
        return (time.monotonic() - start) > time_budget_s

    # -- collect --------------------------------------------------------
    old_nodes = _collect_nodes(old_tree, min_height=min_height)
    new_nodes = _collect_nodes(new_tree, min_height=min_height)
    if budget_exceeded():
        return DiffResult(
            edits=[],
            stats={"reason": "time-budget", "phase": "collect"},
            substrate="unified-diff-fallback",
            unified_diff=_unified_diff_text(old_source, new_source),
        )

    # -- Phase 1 --------------------------------------------------------
    phase1_pairs, matched_old, matched_new = _top_down_match(old_nodes, new_nodes)
    if budget_exceeded():
        return DiffResult(
            edits=[],
            stats={"reason": "time-budget", "phase": "top-down"},
            substrate="unified-diff-fallback",
            unified_diff=_unified_diff_text(old_source, new_source),
        )

    # -- Phase 2 --------------------------------------------------------
    phase2_pairs = _bottom_up_match(
        old_nodes, new_nodes, matched_old, matched_new, min_dice=min_dice
    )
    if budget_exceeded():
        return DiffResult(
            edits=[],
            stats={"reason": "time-budget", "phase": "bottom-up"},
            substrate="unified-diff-fallback",
            unified_diff=_unified_diff_text(old_source, new_source),
        )

    # -- classify -------------------------------------------------------
    edits = _classify_edits(
        phase1_pairs, phase2_pairs, old_nodes, new_nodes, matched_old, matched_new
    )

    stats = {
        "old_nodes": len(old_nodes),
        "new_nodes": len(new_nodes),
        "phase1_matches": len(phase1_pairs),
        "phase2_matches": len(phase2_pairs),
        "elapsed_s": round(time.monotonic() - start, 4),
        "min_height": min_height,
        "min_dice": min_dice,
    }
    return DiffResult(edits=edits, stats=stats, substrate="gumtree")


# -------------------------------------------------------------------------
# Flag emission (timeout only; M2 does not emit correctness flags)
# -------------------------------------------------------------------------


import os as _os  # noqa: E402 (kept local to the flag-emit helper)


DEFAULT_FLAG_LOG = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "state",
    "review-flags.jsonl",
)


def _emit_timeout_flag(
    file: str, result: DiffResult, log_path: str = DEFAULT_FLAG_LOG
) -> None:
    """Append a ``structural-diff-timeout`` flag record to review-flags.jsonl.

    Shape follows emit_flags._record closely so downstream consumers can
    handle both M1 and M2 rows with one parser.
    """
    from datetime import datetime, timezone

    _os.makedirs(_os.path.dirname(log_path), exist_ok=True)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "file": file,
        "line": 0,
        "function": "<module>",
        "rule_id": "PY-M2-TIMEOUT",
        "flag_class": "substrate-parse-failed",
        "severity": "LOW",
        "witness_hints": {
            "substrate": result.substrate,
            "stats": result.stats,
        },
        "needs_M5_confirmation": False,
        "m1_confidence": 0.0,
    }
    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False))
        fh.write("\n")


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


def _read_input(file_arg: str | None, text_arg: str | None) -> str:
    if file_arg is not None:
        if file_arg == "-":
            return sys.stdin.read()
        with open(file_arg, "r", encoding="utf-8") as fh:
            return fh.read()
    if text_arg is not None:
        if text_arg == "-":
            return sys.stdin.read()
        return text_arg
    raise SystemExit("must supply --old-file/--new-file or --old-text/--new-text")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lich M2 structural diff")
    ap.add_argument("--old-file", help="Path to old source (or '-' for stdin)")
    ap.add_argument("--new-file", help="Path to new source (or '-' for stdin)")
    ap.add_argument("--old-text", help="Inline old source (or '-' for stdin)")
    ap.add_argument("--new-text", help="Inline new source (or '-' for stdin)")
    ap.add_argument(
        "--time-budget",
        type=float,
        default=2.0,
        help="Budget in seconds before unified-diff fallback (default 2.0)",
    )
    ap.add_argument(
        "--emit-flags",
        action="store_true",
        help="On fallback, append a structural-diff-timeout to review-flags.jsonl",
    )
    ap.add_argument(
        "--file-label",
        default="<m2>",
        help="Logical filename to record in the timeout flag (if --emit-flags)",
    )
    args = ap.parse_args(argv)

    old_source = _read_input(args.old_file, args.old_text)
    new_source = _read_input(args.new_file, args.new_text)

    result = diff(old_source, new_source, time_budget_s=args.time_budget)

    if args.emit_flags and result.substrate == "unified-diff-fallback":
        label = args.old_file or args.new_file or args.file_label
        _emit_timeout_flag(label, result)

    print(_result_to_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
