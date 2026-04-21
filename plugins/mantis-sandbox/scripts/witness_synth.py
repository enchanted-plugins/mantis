"""Mantis M5 sandbox — witness synthesis.

Given a flag_class + witness_hints from the M1 walker, produce a list of
concrete boundary-value witnesses to feed the sandboxed runner.

A Witness is `{"args": list, "kwargs": dict, "reason": str}` — the
`reason` is kept for the run-log so a confirmed bug points back at which
boundary value reproduced it.

Signature probing is AST-based (stdlib `ast`) — we reopen the target
file, find the `FunctionDef` matching `function_name`, and extract
positional + keyword arg names. We do NOT import the module (importing
is the runner's job, inside the sandbox). Type annotations are read as
strings via `ast.unparse` when present; absence of annotations is the
common case and is handled by defaulting to flag-class-appropriate
boundary values.
"""

from __future__ import annotations

import ast
from typing import Any

# Flag class -> ordered list of (arg_value, reason) pairs. Ordered so the
# first witness is the most-likely-to-trigger value; a later witness is
# only tried if the earlier one produced no-bug (cheap quit).
_DIV_ZERO_VALUES: list[tuple[Any, str]] = [
    (0, "int-zero-denominator"),
    (0.0, "float-zero-denominator"),
]

_INDEX_OOB_EMPTY: list[tuple[Any, str]] = [
    ([], "empty-collection"),
    ([0], "single-element-collection"),
]

_NULL_DEREF_VALUES: list[tuple[Any, str]] = [
    (None, "none-target"),
]


def _parse_signature(source: str, function_name: str) -> list[str] | None:
    """Return the list of positional arg names for `function_name`, or
    None if the function is not found or the source cannot be parsed.
    Methods are handled by dropping a leading `self` / `cls`."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name != function_name:
                continue
            args = [a.arg for a in node.args.args]
            if args and args[0] in ("self", "cls"):
                args = args[1:]
            return args
    return None


def _fill_args(arg_names: list[str], fill_value: Any) -> list[Any]:
    """Produce a positional args list: the first arg gets the boundary
    value; the rest get harmless defaults (0)."""
    if not arg_names:
        return []
    out: list[Any] = [fill_value]
    out.extend(0 for _ in arg_names[1:])
    return out


def synthesize(
    flag_class: str,
    witness_hints: dict,
    target_file: str,
    function_name: str,
) -> list[dict]:
    """Return a list of witness dicts for `flag_class`.

    If the signature cannot be parsed, returns an empty list — the
    orchestrator treats this as `input-synthesis-failed`.
    """
    # M4 Type-Reflected Invariant Synthesis — advisory upstream step.
    # If M4 produces typed witnesses, use them; on any failure, fall
    # through to the generic boundary-value path below.
    try:
        from m4_invariant_synth import synthesize_typed
        typed = synthesize_typed(target_file, function_name, flag_class)
        if typed:
            return typed
    except Exception:
        pass

    try:
        with open(target_file, "r", encoding="utf-8") as fh:
            source = fh.read()
    except OSError:
        return []

    arg_names = _parse_signature(source, function_name)
    if arg_names is None:
        return []

    if flag_class == "div-zero":
        values = _DIV_ZERO_VALUES
    elif flag_class == "index-oob":
        values = _INDEX_OOB_EMPTY
    elif flag_class == "null-deref":
        values = _NULL_DEREF_VALUES
    else:
        return []

    # Prefer the M1 walker's hints if present — boundary_values from
    # witness_hints override the default set when they match an arg type.
    hint_values = witness_hints.get("boundary_values") if witness_hints else None
    witnesses: list[dict] = []

    # First emit M1 hint-derived witnesses (explicit boundary_values).
    if hint_values:
        for hv in hint_values:
            witnesses.append({
                "args": _fill_args(arg_names, hv),
                "kwargs": {},
                "reason": f"m1-hint:{hv!r}",
            })

    # Then emit the canonical boundary-value set for the flag class.
    for val, reason in values:
        witnesses.append({
            "args": _fill_args(arg_names, val),
            "kwargs": {},
            "reason": reason,
        })

    # De-duplicate on (args, kwargs) — hint and canonical may overlap.
    seen: set[str] = set()
    unique: list[dict] = []
    for w in witnesses:
        # repr is stable enough for de-dup given the restricted value domain.
        key = repr((w["args"], w["kwargs"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(w)

    return unique
