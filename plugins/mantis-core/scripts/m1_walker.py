"""Mantis M1 walker — stdlib `ast`-only static analyzer for runtime-failure flags.

Emits flags consumed by Agent 3's M5 sandbox for confirmation. Zero external
deps (stdlib `ast` only). Scope is correctness (div-zero, index-oob,
null-deref); security findings are Reaper's lane.

Abstract interpretation is deliberately lite: per-function, we track names
that are "possibly None" (assigned from a risky call) or "possibly empty"
(assigned from a list-comp / split / filter). Guard patterns (`if x:`,
`if not x:`, `if x is None`, `len(x) != 0`, early-return on None) clear
the taint within their True/False branch.

Rules:
    PY-M1-001 div-zero     — `/`, `//`, `%` with a denominator whose abstract
                             value can include 0.
    PY-M1-002 index-oob    — `x[int]` or `x[-n]` on a value whose length is
                             unknown or possibly zero.
    PY-M1-003 null-deref   — `.attr` or `[...]` on a name assigned from a
                             call known to return Optional, with no guard.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Iterator


# -------------------------------------------------------------------------
# Abstract value tags
# -------------------------------------------------------------------------

TAG_POSSIBLY_NONE = "possibly_none"
TAG_POSSIBLY_EMPTY = "possibly_empty"
TAG_UNKNOWN_LEN = "unknown_len"

# Calls whose return value is Optional-like (may be None).
OPTIONAL_RETURN_METHODS = frozenset({"get", "match", "search", "fullmatch", "find"})
OPTIONAL_RETURN_BUILTINS = frozenset({"next"})  # next(it, None) defaults to None

# Calls whose return value may be empty-sequence-like.
POSSIBLY_EMPTY_METHODS = frozenset({"split", "rsplit", "splitlines", "findall"})
POSSIBLY_EMPTY_BUILTINS = frozenset({"filter", "list", "tuple"})


# -------------------------------------------------------------------------
# Flag record
# -------------------------------------------------------------------------


@dataclass
class Flag:
    file: str
    line: int
    function: str
    rule_id: str
    flag_class: str
    severity: str
    witness_hints: dict
    needs_M5_confirmation: bool = True
    m1_confidence: float = 0.9


# -------------------------------------------------------------------------
# Expression helpers
# -------------------------------------------------------------------------


def _expr_src(node: ast.AST) -> str:
    """Best-effort source rendering for a node — falls back to ast.dump."""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover — 3.8 without unparse
        return ast.dump(node)


def _is_zero_literal(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


def _is_len_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "len"
    )


def _optional_call_kind(node: ast.AST) -> str | None:
    """If `node` is a call whose return can be None, return a hint string."""
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    # dict.get(x) with no default — d.get(k)
    if isinstance(func, ast.Attribute):
        if func.attr == "get" and len(node.args) == 1 and not node.keywords:
            return "dict.get-no-default"
        if func.attr in OPTIONAL_RETURN_METHODS:
            return f".{func.attr}()"
    # next(it, None) / next(it)
    if isinstance(func, ast.Name):
        if func.id == "next":
            return "next()"
    return None


def _possibly_empty_call_kind(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr in POSSIBLY_EMPTY_METHODS:
        return f".{func.attr}()"
    if isinstance(func, ast.Name) and func.id in POSSIBLY_EMPTY_BUILTINS:
        return f"{func.id}()"
    return None


# -------------------------------------------------------------------------
# Per-function environment
# -------------------------------------------------------------------------


@dataclass
class Env:
    """Name -> set of taint tags within the current function body."""
    function: str
    tags: dict[str, set[str]] = field(default_factory=dict)
    guarded: set[str] = field(default_factory=set)

    def tag(self, name: str, tag: str) -> None:
        self.tags.setdefault(name, set()).add(tag)

    def has_tag(self, name: str, tag: str) -> bool:
        return tag in self.tags.get(name, set()) and name not in self.guarded

    def guard(self, name: str) -> None:
        self.guarded.add(name)


# -------------------------------------------------------------------------
# Walker
# -------------------------------------------------------------------------


class M1Walker:
    """Walks one Python source file, emitting Flag records."""

    def __init__(self, source: str, file: str):
        self.source = source
        self.file = file
        self.flags: list[Flag] = []

    def walk(self) -> list[Flag]:
        tree = ast.parse(self.source, filename=self.file)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._walk_function(node)
        return self.flags

    # -- function body ----------------------------------------------------

    def _walk_function(self, func: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        env = Env(function=func.name)

        # First pass — harvest assignments and guard predicates in order.
        for stmt in ast.walk(func):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                tgt = stmt.targets[0]
                if isinstance(tgt, ast.Name):
                    self._tag_assignment(env, tgt.id, stmt.value)

        # Gather names that get guarded anywhere in the body. Scope is
        # intentionally coarse (function-wide) — v1 accepts false-negatives
        # on branch-sensitive guards rather than false-positives.
        for stmt in ast.walk(func):
            if isinstance(stmt, ast.If):
                self._collect_guards(env, stmt.test)
            if isinstance(stmt, ast.Assert):
                self._collect_guards(env, stmt.test)

        # Second pass — examine risky expressions.
        for node in ast.walk(func):
            if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Div, ast.FloorDiv, ast.Mod)
            ):
                self._check_div(env, node)
            elif isinstance(node, ast.Subscript):
                self._check_subscript(env, node)
            elif isinstance(node, ast.Attribute):
                self._check_attribute(env, node)

    # -- tagging ----------------------------------------------------------

    def _tag_assignment(self, env: Env, name: str, value: ast.AST) -> None:
        opt = _optional_call_kind(value)
        if opt is not None:
            env.tag(name, TAG_POSSIBLY_NONE)
        empty = _possibly_empty_call_kind(value)
        if empty is not None:
            env.tag(name, TAG_POSSIBLY_EMPTY)
            env.tag(name, TAG_UNKNOWN_LEN)
        if isinstance(value, ast.ListComp):
            env.tag(name, TAG_POSSIBLY_EMPTY)
            env.tag(name, TAG_UNKNOWN_LEN)

    def _collect_guards(self, env: Env, test: ast.AST) -> None:
        """Very small guard recognizer — treats any `if NAME`, `if not NAME`,
        `if NAME is None`, `if NAME is not None`, `len(NAME) ...` as a
        function-wide guard clearing the name's taint."""
        for sub in ast.walk(test):
            if isinstance(sub, ast.Name):
                env.guard(sub.id)
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Name)
                and sub.func.id == "len"
                and sub.args
                and isinstance(sub.args[0], ast.Name)
            ):
                env.guard(sub.args[0].id)

    # -- checks -----------------------------------------------------------

    def _check_div(self, env: Env, node: ast.BinOp) -> None:
        right = node.right
        denom_expr = _expr_src(right)
        flagged = False
        hints: dict = {"denominator_expr": denom_expr, "boundary_values": [0]}

        if _is_zero_literal(right):
            flagged = True
            hints["reason"] = "literal-zero-denominator"
        elif _is_len_call(right):
            # `len(NAME)` — flag unless NAME is guarded (non-empty check).
            inner = right.args[0] if right.args else None
            if isinstance(inner, ast.Name) and inner.id in env.guarded:
                return
            flagged = True
            hints["reason"] = "len()-can-be-zero"
        elif isinstance(right, ast.Name) and env.has_tag(right.id, TAG_UNKNOWN_LEN):
            flagged = True
            hints["reason"] = "unknown-length-denominator"
        elif isinstance(right, ast.Name) and right.id not in env.guarded:
            # Unguarded variable denominator — lower confidence but flag.
            flagged = True
            hints["reason"] = "unguarded-variable-denominator"
            self._emit(
                node.lineno, env.function, "PY-M1-001", "div-zero", "MED",
                hints, m1_confidence=0.55,
            )
            return

        if flagged:
            self._emit(
                node.lineno, env.function, "PY-M1-001", "div-zero", "HIGH",
                hints, m1_confidence=0.9,
            )

    def _check_subscript(self, env: Env, node: ast.Subscript) -> None:
        idx = node.slice
        # Unwrap ast.Index for 3.8 compatibility.
        if hasattr(ast, "Index") and isinstance(idx, ast.Index):  # type: ignore[attr-defined]
            idx = idx.value  # type: ignore[attr-defined]

        value = node.value
        flagged = False
        hints: dict = {"target_expr": _expr_src(value)}

        # Target checks — is it a known-unknown-length producer?
        target_risky = False
        if isinstance(value, ast.ListComp):
            target_risky = True
            hints["reason"] = "listcomp-result-subscript"
        elif _possibly_empty_call_kind(value) is not None:
            target_risky = True
            hints["reason"] = f"{_possibly_empty_call_kind(value)}-result-subscript"
        elif isinstance(value, ast.Name) and env.has_tag(value.id, TAG_UNKNOWN_LEN):
            target_risky = True
            hints["reason"] = "possibly-empty-target"

        # Index checks — integer constant or -n on a possibly-empty target.
        if target_risky and isinstance(idx, ast.Constant) and isinstance(idx.value, int):
            flagged = True
            hints["index_value"] = idx.value
            hints["boundary_values"] = [idx.value]
        elif target_risky and isinstance(idx, ast.UnaryOp) and isinstance(idx.op, ast.USub):
            flagged = True
            hints["index_value"] = "-n"
            hints["boundary_values"] = [-1]

        # Null-deref on subscript: `x[k]` where x is possibly None.
        if isinstance(value, ast.Name) and env.has_tag(value.id, TAG_POSSIBLY_NONE):
            self._emit(
                node.lineno, env.function, "PY-M1-003", "null-deref", "HIGH",
                {"target": value.id, "reason": "subscript-on-possibly-none",
                 "boundary_values": [None]},
                m1_confidence=0.85,
            )

        if flagged:
            self._emit(
                node.lineno, env.function, "PY-M1-002", "index-oob", "HIGH",
                hints, m1_confidence=0.85,
            )

    def _check_attribute(self, env: Env, node: ast.Attribute) -> None:
        value = node.value
        if isinstance(value, ast.Name) and env.has_tag(value.id, TAG_POSSIBLY_NONE):
            self._emit(
                node.lineno, env.function, "PY-M1-003", "null-deref", "HIGH",
                {
                    "target": value.id,
                    "attr": node.attr,
                    "reason": "attribute-on-possibly-none",
                    "boundary_values": [None],
                },
                m1_confidence=0.9,
            )

    # -- emit -------------------------------------------------------------

    def _emit(
        self,
        line: int,
        function: str,
        rule_id: str,
        flag_class: str,
        severity: str,
        hints: dict,
        m1_confidence: float,
    ) -> None:
        self.flags.append(
            Flag(
                file=self.file,
                line=line,
                function=function,
                rule_id=rule_id,
                flag_class=flag_class,
                severity=severity,
                witness_hints=hints,
                needs_M5_confirmation=True,
                m1_confidence=m1_confidence,
            )
        )


# -------------------------------------------------------------------------
# Public API
# -------------------------------------------------------------------------


def analyze(source: str, file: str) -> list[Flag]:
    """Parse `source` and return the list of Flag records."""
    return M1Walker(source, file).walk()


def analyze_path(path: str) -> list[Flag]:
    with open(path, "r", encoding="utf-8") as fh:
        src = fh.read()
    return analyze(src, path)
