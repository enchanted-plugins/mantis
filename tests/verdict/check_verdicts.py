"""Asserts expected per-file verdicts from the v2 M1+M5+verdict pipeline.

Expected verdicts on this Windows host (M5 = platform-unsupported):
  * bad.py            -> FAIL   (>= 3 HIGH M1 flags)
  * high_level.py     -> DEPLOY (no M1 flags, M5 unsupported is not a block)
  * massive_orders.py -> FAIL   (>> 3 HIGH M1 flags)

On POSIX the same files produce the same M1 verdicts; M5 may additionally
escalate to FAIL via confirmed-bug (tighter, not looser). Harness treats
DEPLOY <-> HOLD <-> FAIL with "at least as strict as expected" semantics
— FAIL is allowed where the expectation is HOLD or DEPLOY only if M5
confirmed a runtime failure. Anything weaker than expected is a fail.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_LOG = _REPO / "plugins" / "mantis-verdict" / "state" / "verdict.jsonl"

_EXPECTED = {
    "tests/fixtures/quality-ladder/bad.py": "FAIL",
    "tests/fixtures/quality-ladder/high_level.py": "DEPLOY",
    "tests/fixtures/quality-ladder/massive_orders.py": "FAIL",
}

_ORDER = {"DEPLOY": 0, "HOLD": 1, "FAIL": 2}


def _norm(p: str) -> str:
    return p.replace("\\", "/")


def main() -> int:
    if not _LOG.exists():
        print(f"[fail] verdict log not found: {_LOG}")
        return 1

    records = [
        json.loads(line)
        for line in _LOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    latest: dict[str, dict] = {}
    for r in records:
        latest[_norm(r["file"])] = r

    fails = 0
    for f, want in _EXPECTED.items():
        got_rec = latest.get(f)
        if got_rec is None:
            print(f"[fail] coverage: {f} missing from verdict.jsonl")
            fails += 1
            continue
        got = got_rec["verdict"]
        if _ORDER[got] < _ORDER[want]:
            print(f"[fail] {f}: want>={want} got={got}")
            print(f"        reasons: {got_rec['reasons']}")
            fails += 1
        else:
            conf = got_rec.get("confidence", "?")
            print(f"[pass] {f}: verdict={got} confidence={conf}")

    if fails:
        print(f"[check] FAILED ({fails} issue(s))")
        return 1
    print(f"[check] PASS ({len(_EXPECTED)} file(s) verified)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
