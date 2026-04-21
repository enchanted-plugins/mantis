"""Mantis M5 integration — outcome assertions.

Reads `plugins/mantis-sandbox/state/run-log.jsonl` and verifies that every
fixture under `tests/sandbox/fixtures/` produced at least one record whose
`status` matches the expected outcome class.

Exit codes:
    0 — every assertion passed (including the honest Windows SKIP path).
    1 — at least one assertion failed.
    2 — run-log missing and `--allow-empty` was not passed (deps not ready).

Windows / no-WSL branch: if every record in the log has
`backend == "unsupported"` or `status == "platform-unsupported"`, we assert
*only* that each fixture produced at least one record (no silent drops) and
exit 0 with `PLATFORM SKIP`.

Each assertion prints `[pass] ...` or `[fail] ...` so the harness output
names the failing rule, not "one assertion failed somewhere".
"""

from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
RUN_LOG = os.path.join(
    REPO_ROOT, "plugins", "mantis-sandbox", "state", "run-log.jsonl"
)
FIXTURES_DIR = os.path.join(HERE, "fixtures")


# -----------------------------------------------------------------------------
# Expected outcomes per fixture. The first entry is the primary status; the
# tuple is (primary_status, acceptable_error_classes).
#
# `None` in the error_class tuple means "any error class (or absent) is ok".
# The outcome classifier's acceptable classes per flag_class are the source
# of truth (see mantis-sandbox/scripts/outcome.py::_EXPECTED).
EXPECTED = {
    "confirmed_divzero.py": (
        "confirmed-bug",
        ("ZeroDivisionError",),
    ),
    "confirmed_index_oob.py": (
        "confirmed-bug",
        ("IndexError", "KeyError"),
    ),
    "confirmed_null_deref.py": (
        "confirmed-bug",
        ("AttributeError", "TypeError"),
    ),
    "timeout_infinite.py": (
        "timeout-without-confirmation",
        None,
    ),
    "false_positive.py": (
        "no-bug-found",
        None,
    ),
    "synth_failed.py": (
        "input-synthesis-failed",
        None,
    ),
}


def _load(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for ln, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                records.append(json.loads(raw))
            except json.JSONDecodeError as exc:
                print(
                    f"[warn] run-log.jsonl:{ln} not valid JSON ({exc}); skipping",
                    file=sys.stderr,
                )
    return records


def _fixture_key(record: dict) -> str:
    """Return the fixture filename from a run-log record.

    The sandbox orchestrator nests the originating M1 flag under
    `flag_ref`, so `flag_ref.file` is the primary source. Older/simpler
    record shapes may keep `file` at top level; both are accepted.

    Accepts absolute paths, repo-relative paths, or WSL-translated paths
    (starting with `/mnt/c/...`). Normalisation is intentionally lenient
    because Agent 6's bridge rewrites file paths for the WSL backend.
    """
    flag_ref = record.get("flag_ref") or {}
    path = (
        flag_ref.get("file")
        or record.get("file")
        or record.get("target_file")
        or ""
    )
    # basename is robust across all three path shapes.
    return os.path.basename(path)


def _status(record: dict) -> str:
    return record.get("status") or ""


def _error_class(record: dict) -> str | None:
    return record.get("error_class")


def _signal(record: dict) -> str | None:
    return record.get("signal_name") or record.get("signal")


def _is_platform_unsupported(record: dict) -> bool:
    if _status(record) == "platform-unsupported":
        return True
    if (record.get("backend") or "").lower() == "unsupported":
        return True
    return False


def _group_by_fixture(records: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for rec in records:
        out.setdefault(_fixture_key(rec), []).append(rec)
    return out


# -----------------------------------------------------------------------------
# Assertions. Each returns True/False and prints a per-check line.


def _assert_coverage(by_fix: dict[str, list[dict]]) -> bool:
    ok = True
    for fname in EXPECTED:
        recs = by_fix.get(fname, [])
        if recs:
            print(f"[pass] coverage: {fname} has {len(recs)} record(s)")
        else:
            print(f"[fail] coverage: {fname} has NO records in run-log")
            ok = False
    return ok


def _assert_status(
    fixture: str,
    records: list[dict],
    expected_status: str,
    expected_errors: tuple[str, ...] | None,
) -> bool:
    matches = [r for r in records if _status(r) == expected_status]
    if not matches:
        observed = sorted({_status(r) for r in records})
        print(
            f"[fail] {fixture}: expected status={expected_status!r}, "
            f"observed statuses={observed}"
        )
        return False
    if expected_errors is None:
        print(f"[pass] {fixture}: status={expected_status!r}")
        return True

    ok_errors = [r for r in matches if _error_class(r) in expected_errors]
    if ok_errors:
        err = _error_class(ok_errors[0])
        print(
            f"[pass] {fixture}: status={expected_status!r}, "
            f"error_class={err!r}"
        )
        return True

    observed = sorted({str(_error_class(r)) for r in matches})
    print(
        f"[fail] {fixture}: status={expected_status!r} present but "
        f"error_class {observed} not in {list(expected_errors)}"
    )
    return False


def _assert_timeout_signal(records: list[dict]) -> bool:
    """timeout fixture: at least one record carries SIGALRM (if the backend
    populates signal_name)."""
    sigs = {_signal(r) for r in records if _status(r) == "timeout-without-confirmation"}
    sigs.discard(None)
    if not sigs:
        # Some backends (WSL bridge) may not surface signal_name; accept.
        print(
            "[pass] timeout_infinite.py: signal_name absent "
            "(backend did not populate; tolerated)"
        )
        return True
    if "SIGALRM" in sigs:
        print("[pass] timeout_infinite.py: signal_name includes SIGALRM")
        return True
    print(
        f"[fail] timeout_infinite.py: timeout recorded but signal {sorted(sigs)} "
        "is not SIGALRM"
    )
    return False


# -----------------------------------------------------------------------------


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--allow-empty",
        action="store_true",
        help="exit 0 if run-log is missing or empty (dep-not-ready mode)",
    )
    args = ap.parse_args(argv[1:])

    if not os.path.exists(RUN_LOG):
        msg = f"[check] run-log missing: {RUN_LOG}"
        if args.allow_empty:
            print(msg + " — tolerating (deps not ready)")
            return 0
        print(msg, file=sys.stderr)
        return 2

    records = _load(RUN_LOG)
    if not records:
        msg = f"[check] run-log empty: {RUN_LOG}"
        if args.allow_empty:
            print(msg + " — tolerating (deps not ready)")
            return 0
        print(msg, file=sys.stderr)
        return 2

    by_fix = _group_by_fixture(records)
    print(
        f"[check] {len(records)} record(s) across "
        f"{len(by_fix)} fixture(s)"
    )

    # Coverage is the gate before outcome-specific checks — if a fixture has
    # no records at all, the rest of the checks are undefined.
    coverage_ok = _assert_coverage(by_fix)

    # Windows / platform-unsupported branch. If every observed record is
    # platform-unsupported (or backend=unsupported), we are on a Windows
    # host without a working WSL bridge. Coverage is the only meaningful
    # assertion in that mode — but it IS meaningful: silently dropping a
    # flag instead of recording platform-unsupported is a contract violation.
    if all(_is_platform_unsupported(r) for r in records):
        if coverage_ok:
            print("[check] PLATFORM SKIP (WSL not installed)")
            return 0
        print("[fail] platform-unsupported path missed fixtures (silent drop)")
        return 1

    if not coverage_ok:
        return 1

    ok = True
    for fname, (expected_status, expected_errors) in EXPECTED.items():
        recs = by_fix[fname]
        if not _assert_status(fname, recs, expected_status, expected_errors):
            ok = False

    # Timeout fixture has an extra signal assertion.
    if "timeout_infinite.py" in by_fix:
        if not _assert_timeout_signal(by_fix["timeout_infinite.py"]):
            ok = False

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
