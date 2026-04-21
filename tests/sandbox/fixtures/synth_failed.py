"""Witness target: apply_twice(fn, x) — function takes a callable `fn`.

The arg name `fn` hints at a callable-typed parameter. M1 may flag a
null-deref or index-oob elsewhere; the interesting pathway is that
M5 `witness_synth` cannot manufacture a sensible callable for an
arbitrary positional arg — witness synth's `_fill_args` produces
`[fill_value, 0]` for div-zero / null-deref / index-oob values,
which substitutes `0` (or `None`) into the `fn` slot. The subsequent
call `fn(fn(x))` raises `TypeError: 'int' object is not callable` (or
`'NoneType' is not callable`).

For the v1 orchestrator, the `input-synthesis-failed` status surfaces
when witness_synth returns an empty list — unreachable here because
div-zero/index-oob/null-deref all have fallback canonical values. The
hand_flags.jsonl entry for this fixture therefore uses a `flag_class`
outside the div-zero/index-oob/null-deref set (e.g. `"callable-arg"`)
so witness_synth returns [] and the orchestrator records
`input-synthesis-failed`. This is the deliberate contract probe for
that status class.
"""


def apply_twice(fn, x):
    return fn(fn(x))
