"""Witness target: average(nums) — M1 flags the denominator, but the
function guards against the empty case and returns early.

M1's guard recognizer clears `nums` within the function after the `if
not nums:` branch, so in principle the walker should NOT emit a flag
here. However, the guard recognizer is deliberately coarse (see
m1_walker._collect_guards) and may produce a false-positive PY-M1-001
under some paths — or hand_flags.jsonl forces one to exercise the
no-bug-found path end-to-end.

When the sandbox runs `average(nums=[])`, the early return fires and
the child exits cleanly (exit 0, no traceback). Expected classified
status: `no-bug-found`.
"""


def average(nums):
    if not nums:
        return 0.0
    return sum(nums) / len(nums)
