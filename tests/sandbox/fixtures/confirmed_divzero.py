"""Witness target: average(nums=[]) -> ZeroDivisionError.

M1 should flag the `/ len(nums)` site under PY-M1-001 (div-zero,
`len()-can-be-zero`). M5 witness synth emits `nums=[]`, which in turn
makes `len(nums)` evaluate to 0 and the division raise
ZeroDivisionError. Expected classified status: `confirmed-bug` with
`error_class: "ZeroDivisionError"`.
"""


def average(nums):
    return sum(nums) / len(nums)
