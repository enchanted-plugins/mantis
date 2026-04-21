"""Witness target: greet(users, uid) where users.get(uid) returns None.

M1 should flag the `.name` access on `u` under PY-M1-003 (null-deref)
because `users.get(uid)` with no default is tagged possibly_none and
the attribute access is unguarded. M5 witness synth picks
`users=None` (null-deref boundary value); the body then calls
`None.get(uid)` and the child raises AttributeError. Expected
classified status: `confirmed-bug` with `error_class: "AttributeError"`
or `"TypeError"` (the outcome classifier accepts either for null-deref).
"""


def greet(users, uid):
    u = users.get(uid)
    return f"Hi {u.name}"
