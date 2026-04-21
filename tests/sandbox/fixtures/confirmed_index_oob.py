"""Witness target: first_positive(nums=[]) -> IndexError.

M1 should flag the `[0]` site under PY-M1-002 (index-oob,
`listcomp-result-subscript`). M5 witness synth supplies `nums=[]` so
the comprehension produces an empty list and `[0]` raises IndexError.
Expected classified status: `confirmed-bug` with `error_class: "IndexError"`.
"""


def first_positive(nums):
    return [n for n in nums if n > 0][0]
