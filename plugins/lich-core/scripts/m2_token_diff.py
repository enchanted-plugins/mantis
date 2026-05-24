"""Lich M2 Tier-2 — Token-based Structural Diff Fallback.

A *degraded substrate* for languages where Lich does not ship a real
AST parser. Extracts function-like blocks from lexical tokens, then
pairs old-vs-new by name-equality (fast path) or Dice similarity over
token bags (slow path), matching the threshold used by the Tier-1
GumTree-lite (``min_dice=0.6``).

*** Honest degradation notice ***

This is NOT a real structural diff. It is a token-level approximation:

  - Name-plus-token-bag matching, no AST containment.
  - Estimated precision ~70% of Tier-1 Python GumTree-lite on
    equivalent code (miss rate concentrated in nested/anonymous
    function forms — see "What it misses" below).
  - Emitted with ``substrate="token-diff-fallback"`` so the verdict
    composer can weight it accordingly and the PDF report can
    disclose the degradation (mirrors the honest-skip contract used
    by M5 for ``platform-unsupported``).

What it catches:

  - Top-level ``function name(...)`` / ``func name(...)`` / ``fn
    name(...)`` / ``def name`` / ``name() {{ ... }}`` (shell) form.
  - Top-level ``class`` / ``struct`` / ``module`` / ``interface``
    definitions (same extraction shape, emitted as "class" kind).
  - Insert, delete, update (same name, different token bag), and
    move/rename (different name, Dice >= 0.6).

What it misses (Phase 3 upgrade targets):

  - Nested function declarations (only top-level named decls).
  - Arrow functions, lambdas, anonymous function expressions
    (``const a = () => ...``, ``var x = function() {{}}``).
  - Class methods are captured as part of the enclosing class's
    token bag, not as separate records — rename of a single method
    inside an unchanged class is seen as an ``update`` on the class.
  - Generics, decorators, and attribute syntax are erased into the
    token bag and do not influence pairing.

Zero external runtime deps: stdlib ``re``, ``dataclasses``,
``json``, ``argparse`` only.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field

# Reuse the canonical records from Tier-1 so dispatcher output stays
# type-compatible. This is the one place we cross-import; keeps the
# DiffResult contract single-sourced.
from m2_structural_diff import DiffResult, Edit


# -------------------------------------------------------------------------
# Language families
# -------------------------------------------------------------------------


# Families map a superset of file extensions onto one of three parsing
# strategies. Keys are the family hint names accepted by ``diff()``.
C_LIKE_HINTS = frozenset({"c-like", "ts", "tsx", "js", "jsx", "mjs",
                          "go", "rust", "java", "cpp", "cc", "c"})
RUBY_LIKE_HINTS = frozenset({"ruby-like", "rb", "ruby"})
SHELL_LIKE_HINTS = frozenset({"shell-like", "sh", "bash"})

# C-like function/class signature regex. Captures the identifier name
# at group 'name'. Order matters — match most-specific keywords first.
# Anchored at line start (with optional whitespace) to avoid picking
# up function-typed parameters nested inside other declarations.
_C_LIKE_FUNC_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?:export|public|private|protected|static|async|pub|default)[ \t]+)*"
    r"(?:"
    r"function[ \t]+(?P<fname>[A-Za-z_][A-Za-z0-9_]*)"          # JS/TS: function foo
    r"|func[ \t]+(?P<goname>[A-Za-z_][A-Za-z0-9_]*)"            # Go:    func foo
    r"|fn[ \t]+(?P<rsname>[A-Za-z_][A-Za-z0-9_]*)"              # Rust:  fn foo
    r"|(?:[A-Za-z_][A-Za-z0-9_<>,\[\] \t*&]*?[ \t]+)"           # Java/C: return-type
    r"(?P<jname>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\([^;]*\)[ \t]*\{"
    r")",
    re.MULTILINE,
)

_C_LIKE_CLASS_RE = re.compile(
    r"^[ \t]*"
    r"(?:(?:export|public|private|abstract|final|pub|default)[ \t]+)*"
    r"(?:class|struct|interface|trait|enum)[ \t]+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)

# Ruby-like: ``def name`` / ``class Name`` / ``module Name``. Terminated
# by a matching bare ``end`` at the same indent depth.
_RUBY_DEF_RE = re.compile(
    r"^(?P<indent>[ \t]*)def[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_?!]*)",
    re.MULTILINE,
)
_RUBY_CLASS_RE = re.compile(
    r"^(?P<indent>[ \t]*)(?:class|module)[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.MULTILINE,
)
_RUBY_END_RE = re.compile(r"^(?P<indent>[ \t]*)end[ \t]*$", re.MULTILINE)

# Shell-like: ``name() {`` or ``function name {``. Brace-stack terminated.
_SHELL_FUNC_RE = re.compile(
    r"^[ \t]*(?:function[ \t]+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)[ \t]*\([ \t]*\)[ \t]*\{",
    re.MULTILINE,
)

# Simple string/comment stripper. Three passes per family prevents
# regex false positives on braces inside strings/comments.
_C_LIKE_STRIP = re.compile(
    r"/\*.*?\*/"                       # block comment
    r"|//[^\n]*"                       # line comment
    r"|\"(?:\\.|[^\"\\])*\""           # double-quoted string
    r"|'(?:\\.|[^'\\])*'"              # single-quoted string / char
    r"|`(?:\\.|[^`\\])*`",             # JS template literal
    re.DOTALL,
)
_RUBY_STRIP = re.compile(
    r"=begin[\s\S]*?^=end"             # =begin ... =end
    r"|\#[^\n]*"                       # line comment
    r"|\"(?:\\.|[^\"\\])*\""           # double-quoted
    r"|'(?:\\.|[^'\\])*'",             # single-quoted
    re.MULTILINE,
)
_SHELL_STRIP = re.compile(
    r"\#[^\n]*"
    r"|\"(?:\\.|[^\"\\])*\""
    r"|'(?:[^'])*'",
)


_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


# -------------------------------------------------------------------------
# Function record
# -------------------------------------------------------------------------


@dataclass
class FuncRecord:
    name: str
    kind: str  # "function" | "class"
    start_line: int
    end_line: int
    token_bag: frozenset  # tokens inside the body
    path: str  # e.g. "Module.function[name]"


# -------------------------------------------------------------------------
# Source sanitization
# -------------------------------------------------------------------------


def _strip_strings_and_comments(source: str, family: str) -> str:
    """Replace strings/comments with whitespace of equal length so line
    numbers and subsequent regex offsets stay intact. Prevents
    braces-in-strings from corrupting block-boundary detection.
    """
    if family == "c-like":
        pattern = _C_LIKE_STRIP
    elif family == "ruby-like":
        pattern = _RUBY_STRIP
    elif family == "shell-like":
        pattern = _SHELL_STRIP
    else:
        return source

    def blank(m: re.Match) -> str:
        # Preserve newlines so line numbers don't shift; blank out the rest.
        return "".join(ch if ch == "\n" else " " for ch in m.group(0))

    return pattern.sub(blank, source)


def _line_of(source: str, offset: int) -> int:
    """1-based line number for a character offset."""
    return source.count("\n", 0, offset) + 1


# -------------------------------------------------------------------------
# Block extraction per family
# -------------------------------------------------------------------------


def _match_closing_brace(source: str, open_offset: int) -> int:
    """Return offset *after* the matching ``}`` for the ``{`` at
    ``open_offset``, or len(source) if unmatched. Assumes the source
    has already been string/comment-stripped so braces are real.
    """
    depth = 0
    i = open_offset
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _extract_c_like(source: str) -> list[FuncRecord]:
    """C-like families: Go, Rust, Java, C/C++, JS/TS. Functions and
    classes are detected by regex; bodies are brace-balanced.
    """
    stripped = _strip_strings_and_comments(source, "c-like")
    records: list[FuncRecord] = []

    # Functions
    for m in _C_LIKE_FUNC_RE.finditer(stripped):
        name = (m.group("fname") or m.group("goname")
                or m.group("rsname") or m.group("jname"))
        if not name:
            continue
        # Guard against Java regex claiming a method that is actually a
        # statement like ``int x = foo();`` — require an opening brace
        # within 200 chars of the match end.
        open_brace = stripped.find("{", m.start(), m.end() + 4)
        if open_brace < 0 or open_brace - m.end() > 4:
            # The C-like regex already requires '{', so this should
            # rarely miss. Keep as a safety net.
            open_brace = stripped.find("{", m.end())
            if open_brace < 0:
                continue
        end = _match_closing_brace(stripped, open_brace)
        body = stripped[open_brace:end]
        records.append(
            FuncRecord(
                name=name,
                kind="function",
                start_line=_line_of(source, m.start()),
                end_line=_line_of(source, end - 1),
                token_bag=frozenset(_IDENT_RE.findall(body)),
                path=f"Module.function[{name}]",
            )
        )

    # Classes / structs / interfaces / traits / enums
    for m in _C_LIKE_CLASS_RE.finditer(stripped):
        name = m.group("name")
        # Keyword guard: ``struct Foo`` is sometimes a forward decl
        # without a body; only record when a body opens.
        open_brace = stripped.find("{", m.end())
        if open_brace < 0:
            continue
        # If the next ';' comes before '{' on the same scope, it's a
        # forward decl.
        semi = stripped.find(";", m.end())
        if 0 <= semi < open_brace:
            continue
        end = _match_closing_brace(stripped, open_brace)
        body = stripped[open_brace:end]
        records.append(
            FuncRecord(
                name=name,
                kind="class",
                start_line=_line_of(source, m.start()),
                end_line=_line_of(source, end - 1),
                token_bag=frozenset(_IDENT_RE.findall(body)),
                path=f"Module.class[{name}]",
            )
        )

    return records


def _find_matching_end(stripped: str, start_offset: int, indent: str) -> int:
    """Ruby-like: find the ``end`` at ``indent`` depth after
    ``start_offset``. Returns offset *after* the ``end`` token.
    Nested ``def``/``class``/``module`` at deeper indent are consumed
    by their own ``end`` — this scanner trusts indent-based nesting,
    which is the practical Ruby convention.
    """
    # Line-by-line scan so we can compare leading whitespace.
    pos = start_offset
    n = len(stripped)
    while pos < n:
        eol = stripped.find("\n", pos)
        if eol < 0:
            eol = n
        line = stripped[pos:eol]
        # Strict indent match; deeper-indented ``end``s belong to nested forms.
        m = re.match(rf"^{re.escape(indent)}end[ \t]*$", line)
        if m:
            return eol
        pos = eol + 1
    return n


def _extract_ruby_like(source: str) -> list[FuncRecord]:
    """Ruby-like: def/end, class/end, module/end. Indent-based nesting."""
    stripped = _strip_strings_and_comments(source, "ruby-like")
    records: list[FuncRecord] = []

    for m in _RUBY_DEF_RE.finditer(stripped):
        indent = m.group("indent")
        name = m.group("name")
        end_offset = _find_matching_end(stripped, m.end(), indent)
        body = stripped[m.end():end_offset]
        records.append(
            FuncRecord(
                name=name,
                kind="function",
                start_line=_line_of(source, m.start()),
                end_line=_line_of(source, max(end_offset - 1, m.start())),
                token_bag=frozenset(_IDENT_RE.findall(body)),
                path=f"Module.function[{name}]",
            )
        )

    for m in _RUBY_CLASS_RE.finditer(stripped):
        indent = m.group("indent")
        name = m.group("name")
        end_offset = _find_matching_end(stripped, m.end(), indent)
        body = stripped[m.end():end_offset]
        records.append(
            FuncRecord(
                name=name,
                kind="class",
                start_line=_line_of(source, m.start()),
                end_line=_line_of(source, max(end_offset - 1, m.start())),
                token_bag=frozenset(_IDENT_RE.findall(body)),
                path=f"Module.class[{name}]",
            )
        )

    return records


def _extract_shell_like(source: str) -> list[FuncRecord]:
    """Shell: name() { ... } or function name { ... }. Brace-balanced."""
    stripped = _strip_strings_and_comments(source, "shell-like")
    records: list[FuncRecord] = []

    for m in _SHELL_FUNC_RE.finditer(stripped):
        name = m.group("name")
        # Skip bash keywords that look like function calls.
        if name in {"if", "while", "for", "until", "case", "select"}:
            continue
        open_brace = stripped.find("{", m.start())
        if open_brace < 0:
            continue
        end = _match_closing_brace(stripped, open_brace)
        body = stripped[open_brace:end]
        records.append(
            FuncRecord(
                name=name,
                kind="function",
                start_line=_line_of(source, m.start()),
                end_line=_line_of(source, end - 1),
                token_bag=frozenset(_IDENT_RE.findall(body)),
                path=f"Module.function[{name}]",
            )
        )

    return records


def _extract(source: str, family: str) -> list[FuncRecord]:
    if family == "c-like":
        return _extract_c_like(source)
    if family == "ruby-like":
        return _extract_ruby_like(source)
    if family == "shell-like":
        return _extract_shell_like(source)
    return []


# -------------------------------------------------------------------------
# Pairing + classification
# -------------------------------------------------------------------------


def _dice(a: frozenset, b: frozenset) -> float:
    """Dice coefficient over two token sets."""
    if not a and not b:
        return 1.0
    inter = len(a & b)
    denom = len(a) + len(b)
    if denom == 0:
        return 0.0
    return (2.0 * inter) / denom


def _classify(
    old_records: list[FuncRecord],
    new_records: list[FuncRecord],
    min_dice: float,
) -> list[Edit]:
    edits: list[Edit] = []
    used_old: set[int] = set()
    used_new: set[int] = set()

    # -- Pass 1: exact name match --------------------------------------
    new_by_name: dict[str, list[int]] = {}
    for i, r in enumerate(new_records):
        new_by_name.setdefault((r.kind, r.name), []).append(i)

    for oi, old in enumerate(old_records):
        candidates = new_by_name.get((old.kind, old.name), [])
        for ni in candidates:
            if ni in used_new:
                continue
            new = new_records[ni]
            # Same name + same token bag → no edit (silent).
            # Same name + different token bag → update.
            if old.token_bag != new.token_bag:
                sim = _dice(old.token_bag, new.token_bag)
                edits.append(
                    Edit(
                        action="update",
                        old_path=old.path,
                        new_path=new.path,
                        old_type=old.kind,
                        new_type=new.kind,
                        similarity=sim,
                    )
                )
            used_old.add(oi)
            used_new.add(ni)
            break

    # -- Pass 2: Dice over unmatched (rename / move) -------------------
    candidates: list[tuple[float, int, int]] = []
    for oi, old in enumerate(old_records):
        if oi in used_old:
            continue
        for ni, new in enumerate(new_records):
            if ni in used_new:
                continue
            if old.kind != new.kind:
                continue
            sim = _dice(old.token_bag, new.token_bag)
            if sim >= min_dice:
                candidates.append((sim, oi, ni))

    candidates.sort(key=lambda t: -t[0])
    for sim, oi, ni in candidates:
        if oi in used_old or ni in used_new:
            continue
        old = old_records[oi]
        new = new_records[ni]
        edits.append(
            Edit(
                action="move",
                old_path=old.path,
                new_path=new.path,
                old_type=old.kind,
                new_type=new.kind,
                similarity=sim,
            )
        )
        used_old.add(oi)
        used_new.add(ni)

    # -- Pass 3: unmatched → delete / insert --------------------------
    for oi, old in enumerate(old_records):
        if oi in used_old:
            continue
        edits.append(
            Edit(
                action="delete",
                old_path=old.path,
                new_path=None,
                old_type=old.kind,
                new_type=None,
                similarity=0.0,
            )
        )
    for ni, new in enumerate(new_records):
        if ni in used_new:
            continue
        edits.append(
            Edit(
                action="insert",
                old_path=None,
                new_path=new.path,
                old_type=None,
                new_type=new.kind,
                similarity=0.0,
            )
        )

    return edits


# -------------------------------------------------------------------------
# Top-level entry
# -------------------------------------------------------------------------


def _normalize_hint(language_hint: str) -> str:
    """Map a raw extension or family alias to one of the three
    canonical family names, or ``"unsupported"``.
    """
    h = language_hint.lower().lstrip(".")
    if h in C_LIKE_HINTS:
        return "c-like"
    if h in RUBY_LIKE_HINTS:
        return "ruby-like"
    if h in SHELL_LIKE_HINTS:
        return "shell-like"
    return "unsupported"


def diff(
    old_source: str,
    new_source: str,
    language_hint: str = "generic",
    min_dice: float = 0.6,
) -> DiffResult:
    """Token-based structural diff (Tier-2 fallback).

    Returns a ``DiffResult`` with ``substrate="token-diff-fallback"``
    on success, or ``substrate="m2-unsupported-language"`` when the
    hint does not resolve to a known family (honest-skip contract).
    """
    family = _normalize_hint(language_hint)
    if family == "unsupported":
        return DiffResult(
            edits=[],
            stats={"reason": "unsupported-language", "hint": language_hint},
            substrate="m2-unsupported-language",
        )

    old_records = _extract(old_source, family)
    new_records = _extract(new_source, family)
    edits = _classify(old_records, new_records, min_dice=min_dice)

    stats = {
        "family": family,
        "old_records": len(old_records),
        "new_records": len(new_records),
        "min_dice": min_dice,
        "degradation_note": "token-based approximation; ~70% of Tier-1 precision",
    }
    return DiffResult(edits=edits, stats=stats, substrate="token-diff-fallback")


# -------------------------------------------------------------------------
# CLI (thin — primary CLI lives in m2_dispatcher.py)
# -------------------------------------------------------------------------


def _result_to_json(result: DiffResult) -> str:
    payload = {
        "substrate": result.substrate,
        "stats": result.stats,
        "edits": [asdict(e) for e in result.edits],
    }
    return json.dumps(payload, indent=2, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lich M2 Tier-2 token diff")
    ap.add_argument("--old-file", required=True)
    ap.add_argument("--new-file", required=True)
    ap.add_argument("--language", default="c-like")
    ap.add_argument("--min-dice", type=float, default=0.6)
    args = ap.parse_args(argv)

    with open(args.old_file, "r", encoding="utf-8") as fh:
        old = fh.read()
    with open(args.new_file, "r", encoding="utf-8") as fh:
        new = fh.read()

    result = diff(old, new, language_hint=args.language, min_dice=args.min_dice)
    print(_result_to_json(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
