"""Lich M5 sandbox — outcome classifier.

Maps (flag_class, exit_code, stderr, signal) -> (status, error_class).

`status` is one of the six canonical run-log statuses:
    confirmed-bug | timeout-without-confirmation | no-bug-found
    | input-synthesis-failed | sandbox-error | platform-unsupported

The flag_class -> expected error_class correspondence is deliberately
narrow: a ZeroDivisionError only confirms a div-zero flag, not an
index-oob flag. If the child raises an unrelated exception (a different
bug class), v1 returns `no-bug-found` rather than speculate — M1 will
re-flag it on the next pass under the correct rule.
"""

from __future__ import annotations

import re

# Expected exception classes per flag_class. Multiple acceptable
# classes per flag since Python's error taxonomy is not perfectly
# aligned with the flag taxonomy (e.g. null-deref surfaces as either
# AttributeError on .attr or TypeError on None subscript/call).
_EXPECTED: dict[str, frozenset[str]] = {
    "div-zero": frozenset({"ZeroDivisionError"}),
    "index-oob": frozenset({"IndexError", "KeyError"}),
    "null-deref": frozenset({"AttributeError", "TypeError"}),
}

# Regex extracting `ErrorClass: message` on the last non-empty traceback line.
_TRACEBACK_TAIL = re.compile(r"^(\w+(?:Error|Exception)):", re.MULTILINE)


def _extract_error_class(stderr: str) -> str | None:
    """Return the last `XxxError` / `XxxException` class name in `stderr`."""
    if not stderr:
        return None
    matches = _TRACEBACK_TAIL.findall(stderr)
    return matches[-1] if matches else None


def classify(
    flag_class: str,
    exit_code: int,
    stderr: str,
    signal_name: str | None,
) -> tuple[str, str | None]:
    """Return (status, error_class).

    Precedence:
        1. SIGALRM anywhere -> timeout-without-confirmation (child alarm).
        2. SIGXFSZ -> sandbox-error (write cap exceeded; not a finding).
        3. SIGKILL with cap-exceeded markers -> sandbox-error (AS/CPU).
        4. exit 0 and clean stderr -> no-bug-found.
        5. exit != 0 with matching error class -> confirmed-bug.
        6. exit != 0 with mismatched error -> no-bug-found (v1 policy).
    """
    # Signal-driven outcomes first.
    if signal_name == "SIGALRM":
        return "timeout-without-confirmation", None
    if signal_name == "SIGXFSZ":
        return "sandbox-error", "WriteCapExceeded"
    if signal_name == "SIGKILL":
        # Heuristic: if stderr carries MemoryError / resource markers, it's
        # the AS cap. Either way this is infra, not a finding.
        return "sandbox-error", "ResourceCapExceeded"

    # Clean run.
    if exit_code == 0 and _extract_error_class(stderr) is None:
        return "no-bug-found", None

    # Non-zero exit with a traceback.
    err = _extract_error_class(stderr)
    expected = _EXPECTED.get(flag_class, frozenset())

    if err is not None and err in expected:
        return "confirmed-bug", err

    # Some null-deref cases surface as TypeError with 'NoneType' in the
    # message even when the class match above already caught TypeError;
    # this branch handles a KeyError dressed as a generic Exception etc.
    if err is not None and flag_class == "null-deref" and "NoneType" in (stderr or ""):
        return "confirmed-bug", err

    # Non-zero exit, unrelated exception class — v1 does not cross-classify.
    return "no-bug-found", err
