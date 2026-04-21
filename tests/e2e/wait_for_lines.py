"""Poll a JSONL file until its line count grows by an expected delta.

Usage:
    python wait_for_lines.py --path <jsonl> --min-lines <N> --timeout-s <S>
                             [--baseline <B>]

Semantics:
    - Waits until the file has at least `min-lines` non-empty lines total
      (or, when --baseline is given, at least `baseline + min-lines`).
    - Default baseline is 0: "has this file reached N total lines yet?".
    - Polls at ~50ms cadence; returns as soon as the condition is met.

Output: one JSON object to stdout:
    {"path": ..., "observed_lines": N, "elapsed_ms": M, "timed_out": bool}

Exit codes:
    0 — condition met within the timeout
    1 — timeout reached without hitting the line count
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def _count_nonempty(p: Path) -> int:
    if not p.exists():
        return 0
    n = 0
    with p.open("rb") as f:
        for line in f:
            if line.strip():
                n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", required=True, type=Path)
    ap.add_argument("--min-lines", required=True, type=int,
                    help="When --baseline given, this is the delta; "
                         "otherwise it's the absolute minimum total.")
    ap.add_argument("--timeout-s", required=True, type=float)
    ap.add_argument("--baseline", type=int, default=0,
                    help="Pre-existing line count to subtract from 'observed' "
                         "when checking the condition.")
    args = ap.parse_args()

    target_total = args.baseline + args.min_lines
    start = time.monotonic()
    deadline = start + args.timeout_s
    observed = _count_nonempty(args.path)

    while observed < target_total:
        if time.monotonic() >= deadline:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            print(json.dumps({
                "path": str(args.path),
                "observed_lines": observed,
                "baseline": args.baseline,
                "target_total": target_total,
                "elapsed_ms": elapsed_ms,
                "timed_out": True,
            }))
            return 1
        time.sleep(0.05)
        observed = _count_nonempty(args.path)

    elapsed_ms = int((time.monotonic() - start) * 1000)
    print(json.dumps({
        "path": str(args.path),
        "observed_lines": observed,
        "baseline": args.baseline,
        "target_total": target_total,
        "elapsed_ms": elapsed_ms,
        "timed_out": False,
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
