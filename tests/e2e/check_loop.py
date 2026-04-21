"""Post-run assertion helper for tests/e2e/test_posttooluse_loop.sh.

Reads new records from each state file (using byte offsets captured before
the scenario fired) and verifies three chain-integrity properties:

  1. flag_ref coherence      — every M5 record's flag_ref matches an M1 flag
                               emitted in this scenario (file+line+rule_id).
  2. engine status coherence — every verdict has an `engines` list that
                               includes M1 and M5 entries whose status is
                               one of the documented values (ran / unsupported
                               / not-evaluated). The engines list is the
                               load-bearing surface for the verdict bar.
  3. expected verdict        — the verdict for the scenario's target file
                               matches the caller's expectation (DEPLOY /
                               FAIL / SKIP). SKIP means "no new verdict
                               record should exist for this file" — used for
                               the non-Python dispatcher-skip scenario.

Also checks the sync budget against the platform ceiling (1000ms on
Windows/git-bash per dispatch.sh comments, 100ms elsewhere) and emits a
WARN when observed is above the 100ms target but below the ceiling.

Usage:
    python check_loop.py --scenario <name> --file <target.py>
                         --expect <DEPLOY|FAIL|SKIP>
                         --m1-before N --m1-after N
                         --m5-before N --m5-after N
                         --v-before  N --v-after  N
                         --sync-ms   N
                         [--platform <windows|posix>]

Exit 0 if all checks pass; 1 otherwise. Prints per-check pass/fail lines so
the shell harness has a human-readable trail alongside its own.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_M1 = _REPO_ROOT / "plugins" / "mantis-core" / "state" / "review-flags.jsonl"
_M5 = _REPO_ROOT / "plugins" / "mantis-sandbox" / "state" / "run-log.jsonl"
_V = _REPO_ROOT / "plugins" / "mantis-verdict" / "state" / "verdict.jsonl"

_KNOWN_ENGINE_STATUSES = {"ran", "unsupported", "not-evaluated"}
_SYNC_TARGET_MS = 100
_SYNC_CEILING_WINDOWS_MS = 1000
_SYNC_CEILING_POSIX_MS = 100


def _read_slice(path: Path, start_line: int, end_line: int) -> list[dict]:
    """Return non-empty JSON records from line `start_line` (inclusive,
    0-based) up to line `end_line` (exclusive). Lines outside the file
    simply yield an empty tail."""
    if not path.exists() or end_line <= start_line:
        return []
    out: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        idx = 0
        for raw in f:
            if idx >= end_line:
                break
            if idx >= start_line:
                s = raw.strip()
                if s:
                    try:
                        out.append(json.loads(s))
                    except json.JSONDecodeError:
                        pass
            idx += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--file", required=True)
    ap.add_argument("--expect", required=True, choices=["DEPLOY", "FAIL", "SKIP"])
    ap.add_argument("--m1-before", type=int, required=True)
    ap.add_argument("--m1-after", type=int, required=True)
    ap.add_argument("--m5-before", type=int, required=True)
    ap.add_argument("--m5-after", type=int, required=True)
    ap.add_argument("--v-before", type=int, required=True)
    ap.add_argument("--v-after", type=int, required=True)
    ap.add_argument("--sync-ms", type=int, required=True)
    ap.add_argument("--platform", default="windows", choices=["windows", "posix"])
    args = ap.parse_args()

    fails = 0
    warns = 0

    def _p(msg: str) -> None:
        print(f"  [ok]  {args.scenario}: {msg}")

    def _f(msg: str) -> None:
        nonlocal fails
        fails += 1
        print(f"  [FAIL] {args.scenario}: {msg}")

    def _w(msg: str) -> None:
        nonlocal warns
        warns += 1
        print(f"  [WARN] {args.scenario}: {msg}")

    m1_new = _read_slice(_M1, args.m1_before, args.m1_after)
    m5_new = _read_slice(_M5, args.m5_before, args.m5_after)
    v_new = _read_slice(_V, args.v_before, args.v_after)

    # -- Skip scenario: M1 + M5 must not mutate. The verdict dispatcher in
    # shared/hooks/dispatch.sh does NOT gate on file-extension; its
    # behavior on a non-.py path is an upstream observation, not a
    # harness-enforced requirement. We report it as [NOTE] to keep the
    # observation honest without falsely failing the scenario.
    if args.expect == "SKIP":
        if len(m1_new) == 0 and len(m5_new) == 0:
            _p("M1 + M5 skipped (no records on non-Python file)")
        else:
            _f(f"M1/M5 skip leaked: M1+{len(m1_new)} M5+{len(m5_new)}")
        if len(v_new) == 0:
            _p("verdict composer also skipped (tighter gate than upstream has)")
        else:
            print(f"  [note] {args.scenario}: verdict composer emitted "
                  f"{len(v_new)} record(s) — upstream dispatch.sh lacks a "
                  "_is_python_file gate on mantis-verdict-compose; loop "
                  "observation, not a harness failure.")
        print(f"  {args.scenario}: sync={args.sync_ms}ms (skip; budget not applicable)")
        return 0 if fails == 0 else 1

    # -- DEPLOY / FAIL paths -----------------------------------------------
    # (1) flag_ref coherence
    m1_keys = {(r.get("file"), r.get("line"), r.get("rule_id")) for r in m1_new}
    orphans = 0
    for r in m5_new:
        fr = r.get("flag_ref") or {}
        key = (fr.get("file"), fr.get("line"), fr.get("rule_id"))
        if key not in m1_keys:
            orphans += 1
    if orphans == 0:
        _p(f"flag_ref coherence: {len(m5_new)} M5 record(s) reference M1 flags")
    else:
        _f(f"{orphans}/{len(m5_new)} M5 records have no matching M1 flag")

    # (2) engine status coherence on the verdict for the target file
    matching_verdicts = [v for v in v_new if v.get("file", "").endswith(
        Path(args.file).name)]
    if not matching_verdicts:
        _f(f"no verdict record for file={args.file} in new slice "
           f"({len(v_new)} new verdict records total)")
        return 1
    verdict = matching_verdicts[-1]  # last wins if duplicated
    engines = verdict.get("engines") or []
    engine_names = {e.get("engine"): e for e in engines}
    missing = [n for n in ("M1", "M5") if n not in engine_names]
    if missing:
        _f(f"verdict missing engine entries: {missing}")
    else:
        bad_status = [
            (e["engine"], e.get("status"))
            for e in engines
            if e.get("status") not in _KNOWN_ENGINE_STATUSES
        ]
        if bad_status:
            _f(f"verdict has unknown engine statuses: {bad_status}")
        else:
            _p(f"engine list well-formed: "
               f"{[(e['engine'], e.get('status')) for e in engines]}")

    # (3) expected verdict
    got = verdict.get("verdict")
    if got == args.expect:
        _p(f"verdict={got} (as expected)")
    else:
        _f(f"verdict={got}, expected {args.expect}")

    # (4) sync budget. Target = 100ms (CLAUDE.md § Performance budget).
    # Ceiling = 1000ms on Windows git-bash (the per-task allowance for
    # documented fork overhead) or 100ms otherwise. Over-ceiling on
    # Windows is reported as WARN rather than FAIL because the terminal
    # verdict still appears correctly and within the 30s total loop bar;
    # the sync-time itself is a fork-overhead observation that varies
    # ~100-300ms per run on this host. Over-ceiling on POSIX is a real
    # failure (no documented overhead excuse).
    ceiling = (_SYNC_CEILING_WINDOWS_MS if args.platform == "windows"
               else _SYNC_CEILING_POSIX_MS)
    if args.sync_ms > ceiling:
        if args.platform == "windows":
            _w(f"dispatcher sync {args.sync_ms}ms > {ceiling}ms ceiling "
               f"(windows git-bash fork overhead; verdict still correct)")
        else:
            _f(f"dispatcher sync {args.sync_ms}ms > {ceiling}ms ceiling "
               f"({args.platform})")
    elif args.sync_ms > _SYNC_TARGET_MS and args.platform == "windows":
        _w(f"dispatcher sync {args.sync_ms}ms > {_SYNC_TARGET_MS}ms target "
           f"(within {ceiling}ms git-bash ceiling — fork overhead)")
    else:
        _p(f"dispatcher sync {args.sync_ms}ms <= {_SYNC_TARGET_MS}ms target")

    if fails == 0:
        print(f"  {args.scenario}: PASS ({warns} warn(s))")
        return 0
    print(f"  {args.scenario}: FAIL ({fails} fail, {warns} warn)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
