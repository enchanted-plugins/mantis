"""Lich M4 — Type-Reflected Invariant Synthesis.

Reads the target function's type annotations and synthesizes
type-compatible boundary-value witnesses that M5's sandboxed runner can
exercise. M4 is a *consumer* of types, not a type-checker: it does not
validate that a function's annotations are internally consistent; it
only translates them into concrete inputs at the edges of each type's
value lattice.

Inspiration: Python's `hypothesis.strategies.builds()` — which maps a
constructor's signature to an auto-generated search strategy. We're
stdlib-only (brand invariant #1), so there's no real search engine here;
we emit a small deterministic boundary set per type and let M5 run each.

Strategy, in order of preference:

    1. `importlib` + `typing.get_type_hints()` — most accurate; resolves
       string forward-refs and PEP 604 `T | None`. Skipped when the
       target module has side-effectful top-level (import raises).
    2. `ast`-based signature parse + string-type dispatch — safe fallback
       that never imports the target; works for simple primitive and
       container generics.
    3. Return `[]` — caller falls through to the generic witness_synth
       path. Never raise.

The witness schema matches witness_synth's `{"args", "kwargs", "reason"}`
contract so M4 is a drop-in upstream step.
"""

from __future__ import annotations

import ast
import dataclasses as _dc
import importlib.util
import math
import sys
import typing
from pathlib import Path
from typing import Any

# Dataclass recursion cap — prevents runaway synthesis on cyclic or
# deeply-nested type graphs (e.g. linked-list dataclasses). 3 levels is
# plenty for realistic user models while keeping the witness set small.
_MAX_DATACLASS_DEPTH = 3

# ----------------------------------------------------------------------
# Per-type boundary tables. Each entry yields `(value, reason)` pairs.
# Flag-class-aware callers can reorder these (see `_prioritize_for`).
# ----------------------------------------------------------------------

_INT_BOUNDARIES: list[tuple[Any, str]] = [
    (0, "int-zero"),
    (-1, "int-negative-one"),
    (2**31, "int-2^31"),
    (-(2**31), "int-neg-2^31"),
]

_FLOAT_BOUNDARIES: list[tuple[Any, str]] = [
    (0.0, "float-zero"),
    (-0.0, "float-neg-zero"),
    (math.inf, "float-inf"),
    (-math.inf, "float-neg-inf"),
    (math.nan, "float-nan"),
]

_STR_BOUNDARIES: list[tuple[Any, str]] = [
    ("", "str-empty"),
    ("a", "str-single-char"),
    ("\x00", "str-null-byte"),
    ("x" * 1024, "str-very-long"),
]

_BOOL_BOUNDARIES: list[tuple[Any, str]] = [
    (False, "bool-false"),
    (True, "bool-true"),
]

_BYTES_BOUNDARIES: list[tuple[Any, str]] = [
    (b"", "bytes-empty"),
    (b"\x00", "bytes-null"),
]


def _boundaries_for_primitive(t: Any) -> list[tuple[Any, str]] | None:
    """Return boundary values for a primitive type, or None if `t` is
    not a recognized primitive."""
    if t is int:
        return list(_INT_BOUNDARIES)
    if t is float:
        return list(_FLOAT_BOUNDARIES)
    if t is str:
        return list(_STR_BOUNDARIES)
    if t is bool:
        return list(_BOOL_BOUNDARIES)
    if t is bytes:
        return list(_BYTES_BOUNDARIES)
    return None


def _prioritize_for(
    flag_class: str,
    values: list[tuple[Any, str]],
) -> list[tuple[Any, str]]:
    """Promote flag-class-relevant boundaries to the front of `values`
    without dropping any — M5 is cheap and boundary coverage is the goal.
    Returns a new list.
    """
    if not values:
        return values
    if flag_class == "div-zero":
        zeros = [pair for pair in values if pair[0] == 0 or pair[0] == 0.0]
        rest = [pair for pair in values if pair not in zeros]
        return zeros + rest
    if flag_class == "null-deref":
        nones = [pair for pair in values if pair[0] is None]
        rest = [pair for pair in values if pair not in nones]
        return nones + rest
    return values


# ----------------------------------------------------------------------
# Type decomposition
# ----------------------------------------------------------------------


def _is_optional(t: Any) -> tuple[bool, Any]:
    """Detect `Optional[X]` / `X | None`. Returns (is_optional, inner_T)."""
    origin = typing.get_origin(t)
    if origin is typing.Union or (sys.version_info >= (3, 10) and str(origin) == "types.UnionType") \
            or (hasattr(__import__("types"), "UnionType") and origin is getattr(__import__("types"), "UnionType", None)):
        args = [a for a in typing.get_args(t) if a is not type(None)]
        if len(args) == 1 and type(None) in typing.get_args(t):
            return True, args[0]
    return False, None


def _is_list_like(t: Any) -> tuple[bool, Any]:
    origin = typing.get_origin(t)
    if origin in (list, tuple) or t is list:
        args = typing.get_args(t)
        inner = args[0] if args else None
        return True, inner
    return False, None


def _is_dict_like(t: Any) -> tuple[bool, Any, Any]:
    origin = typing.get_origin(t)
    if origin is dict or t is dict:
        args = typing.get_args(t)
        if len(args) == 2:
            return True, args[0], args[1]
        return True, None, None
    return False, None, None


def _is_dataclass(t: Any) -> bool:
    try:
        return _dc.is_dataclass(t) and isinstance(t, type)
    except Exception:
        return False


def _first_boundary(t: Any, depth: int) -> Any:
    """Return *one* representative boundary value of type `t`. Used when
    building container or dataclass instances where the container itself
    is the interesting boundary and we just need a valid inner element."""
    if t is None or t is type(None):
        return None
    prims = _boundaries_for_primitive(t)
    if prims:
        return prims[0][0]
    is_opt, inner = _is_optional(t)
    if is_opt:
        return None
    is_list, inner = _is_list_like(t)
    if is_list:
        return []
    is_dict, _k, _v = _is_dict_like(t)
    if is_dict:
        return {}
    if _is_dataclass(t) and depth < _MAX_DATACLASS_DEPTH:
        return _build_dataclass_boundary(t, depth + 1)
    return None  # last-resort fallback


def _build_dataclass_boundary(cls: type, depth: int) -> Any:
    """Instantiate `cls` with each field set to its type's first boundary.
    Respects `_MAX_DATACLASS_DEPTH`; returns None if depth exceeded or
    instantiation fails."""
    if depth > _MAX_DATACLASS_DEPTH:
        return None
    try:
        kwargs: dict[str, Any] = {}
        for f in _dc.fields(cls):
            kwargs[f.name] = _first_boundary(f.type, depth)
        return cls(**kwargs)
    except Exception:
        return None


def _boundaries_for_type(
    t: Any,
    depth: int = 0,
) -> list[tuple[Any, str]]:
    """Return a list of `(value, reason)` boundary pairs for type `t`.
    An empty list means "no typed boundaries — caller should fall back".
    """
    # None / NoneType
    if t is None or t is type(None):
        return [(None, "none-type")]

    # Primitive
    prims = _boundaries_for_primitive(t)
    if prims is not None:
        return prims

    # Optional / Union-with-None
    is_opt, inner = _is_optional(t)
    if is_opt:
        out: list[tuple[Any, str]] = [(None, "optional-none")]
        out.extend(_boundaries_for_type(inner, depth))
        return out

    # list[T] / Sequence[T] / tuple[T, ...]  (best-effort: treat like list)
    is_list, inner = _is_list_like(t)
    if is_list:
        inner_sample = _first_boundary(inner, depth) if inner is not None else 0
        return [
            ([], "list-empty"),
            ([inner_sample], "list-single-boundary"),
            ([inner_sample] * 100, "list-hundred"),
        ]

    # dict[K, V]
    is_dict, k_t, v_t = _is_dict_like(t)
    if is_dict:
        k_sample = _first_boundary(k_t, depth) if k_t is not None else ""
        v_sample = _first_boundary(v_t, depth) if v_t is not None else 0
        return [
            ({}, "dict-empty"),
            ({k_sample: v_sample}, "dict-single-boundary"),
        ]

    # Dataclass — recurse into fields.
    if _is_dataclass(t) and depth < _MAX_DATACLASS_DEPTH:
        inst = _build_dataclass_boundary(t, depth + 1)
        if inst is not None:
            return [(inst, f"dataclass-{t.__name__}-boundary")]
        return []

    # Unknown / forward ref / unresolvable — caller falls back.
    return []


# ----------------------------------------------------------------------
# Signature resolution
# ----------------------------------------------------------------------


def _resolve_hints_via_import(
    target_file: str,
    function_name: str,
) -> dict[str, Any] | None:
    """Import `target_file` as a throwaway module and read type hints.

    Returns a dict of `{arg_name: resolved_type}`, or None when:
      * module cannot be loaded (side-effectful import, missing dep),
      * function is not a module-level attribute,
      * `get_type_hints()` raises (unresolved forward ref).

    The imported module is NOT registered into `sys.modules` permanently
    — we use a synthetic name to minimize pollution.
    """
    path = Path(target_file)
    if not path.exists():
        return None

    mod_name = f"_m4_probe_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    try:
        # Register briefly so forward-refs that reference the module's
        # own names can resolve, then remove.
        sys.modules[mod_name] = module
        try:
            spec.loader.exec_module(module)  # type: ignore[union-attr]
        except Exception:
            return None

        fn = getattr(module, function_name, None)
        if fn is None or not callable(fn):
            return None
        try:
            hints = typing.get_type_hints(fn)
        except Exception:
            return None
        # Drop the return annotation — we only synthesize inputs.
        hints.pop("return", None)
        return hints
    finally:
        sys.modules.pop(mod_name, None)


def _resolve_hints_via_ast(
    target_file: str,
    function_name: str,
) -> dict[str, str] | None:
    """Parse the target file and return `{arg_name: annotation_string}`.

    Annotations are *strings* (un-evaluated). The caller maps them to a
    small set of recognized primitives — anything more complex falls
    back to the generic witness path.
    """
    try:
        source = Path(target_file).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == function_name:
            out: dict[str, str] = {}
            for a in node.args.args:
                if a.arg in ("self", "cls"):
                    continue
                if a.annotation is None:
                    continue
                try:
                    out[a.arg] = ast.unparse(a.annotation)
                except Exception:
                    continue
            return out
    return None


_AST_STRING_MAP: dict[str, Any] = {
    "int": int, "float": float, "str": str, "bool": bool, "bytes": bytes,
    "None": type(None),
}


def _coerce_ast_annotation(s: str) -> Any:
    """Best-effort map an annotation string to a runtime type. Only
    covers bare primitives — the import path handles everything else."""
    return _AST_STRING_MAP.get(s.strip())


# ----------------------------------------------------------------------
# Public entry
# ----------------------------------------------------------------------


def _divisor_arg_name(arg_names: list[str]) -> str | None:
    """Heuristic: pick an arg that looks like a divisor for div-zero
    prioritization. Returns None if no obvious candidate."""
    for name in arg_names:
        if name in ("n", "d", "denom", "denominator", "divisor", "y", "b"):
            return name
    return None


def synthesize_typed(
    target_file: str,
    function_name: str,
    flag_class: str = "",
) -> list[dict]:
    """Return a list of witness dicts derived from the function's type
    annotations. Empty list signals "no typed witnesses available" —
    the caller (witness_synth.synthesize) should fall through to the
    generic path.

    Never raises on type-resolution failure: every exception path
    returns `[]`.
    """
    try:
        hints_typed = _resolve_hints_via_import(target_file, function_name)
    except Exception:
        hints_typed = None

    arg_names: list[str] = []
    per_arg_boundaries: dict[str, list[tuple[Any, str]]] = {}

    if hints_typed:
        for name, t in hints_typed.items():
            arg_names.append(name)
            bs = _boundaries_for_type(t)
            if bs:
                per_arg_boundaries[name] = bs

    # Fall back to AST dispatch if the import path yielded nothing usable.
    if not per_arg_boundaries:
        hints_str = _resolve_hints_via_ast(target_file, function_name)
        if not hints_str:
            return []
        for name, ann in hints_str.items():
            if name not in arg_names:
                arg_names.append(name)
            t = _coerce_ast_annotation(ann)
            if t is None:
                continue
            bs = _boundaries_for_type(t)
            if bs:
                per_arg_boundaries[name] = bs

    if not per_arg_boundaries:
        return []

    # Also read positional order from AST so we emit positional args in
    # the order they appear in the signature (import path's dict order
    # may differ from source order in some edge cases).
    hints_str_order = _resolve_hints_via_ast(target_file, function_name)
    if hints_str_order:
        ordered = [n for n in hints_str_order.keys() if n in arg_names or n in per_arg_boundaries]
        for n in arg_names:
            if n not in ordered:
                ordered.append(n)
        arg_names = ordered

    witnesses: list[dict] = []
    seen: set[str] = set()

    # Emit one witness per boundary value of each annotated arg; other
    # args get their type's first boundary (or 0 if untyped).
    divisor_name = _divisor_arg_name(arg_names) if flag_class == "div-zero" else None

    for target_arg, bounds in per_arg_boundaries.items():
        bounds_ordered = _prioritize_for(flag_class, bounds)
        # If divisor heuristic fires, only vary the divisor arg for div-zero.
        if divisor_name is not None and target_arg != divisor_name:
            continue
        for val, reason in bounds_ordered:
            args: list[Any] = []
            for name in arg_names:
                if name == target_arg:
                    args.append(val)
                else:
                    default = per_arg_boundaries.get(name)
                    args.append(default[0][0] if default else 0)
            key = repr(args)
            if key in seen:
                continue
            seen.add(key)
            witnesses.append({
                "args": args,
                "kwargs": {},
                "reason": f"m4:{target_arg}={reason}",
            })

    # If the divisor heuristic filtered everything out (e.g. div-zero on
    # a function with no obvious divisor), retry without the filter.
    if not witnesses and divisor_name is not None:
        for target_arg, bounds in per_arg_boundaries.items():
            for val, reason in _prioritize_for(flag_class, bounds):
                args = []
                for name in arg_names:
                    if name == target_arg:
                        args.append(val)
                    else:
                        default = per_arg_boundaries.get(name)
                        args.append(default[0][0] if default else 0)
                key = repr(args)
                if key in seen:
                    continue
                seen.add(key)
                witnesses.append({
                    "args": args,
                    "kwargs": {},
                    "reason": f"m4:{target_arg}={reason}",
                })

    return witnesses
