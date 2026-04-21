"""Helper for tests/verdict/test_stop_hook.sh.

Reads the hook log from a caller-supplied byte offset and asserts whether a
"spawn mantis-verdict-compose" line appears in the tail. Byte-offset slicing
beats timestamp filtering here because `date -Is` is only second-precision
and adjacent scenarios can share a second. Exit 0 on pass, 1 on fail.

Usage:
    python check_stop.py <log_path> <byte_offset> [--expect-absent]

--expect-absent flips the assertion: pass iff no spawn line exists past the
offset. Used by the subagent-guard scenario.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


_MARKER = "spawn mantis-verdict-compose"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("log_path")
    ap.add_argument("offset", type=int)
    ap.add_argument("--expect-absent", action="store_true")
    args = ap.parse_args()

    p = Path(args.log_path)
    if not p.exists():
        if args.expect_absent:
            print(f"[pass] log absent (expected): {p}")
            return 0
        print(f"[fail] log not found: {p}")
        return 1

    with open(p, "rb") as f:
        f.seek(max(0, args.offset))
        tail = f.read().decode("utf-8", errors="replace")

    hits = [line for line in tail.splitlines() if _MARKER in line]

    if args.expect_absent:
        if hits:
            print(f"[fail] spawn seen despite subagent guard: {hits[-1]}")
            return 1
        print("[pass] no spawn past offset")
        return 0

    if not hits:
        print(f"[fail] no '{_MARKER}' line past offset {args.offset}")
        return 1
    print(f"[pass] spawn logged: {hits[-1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
