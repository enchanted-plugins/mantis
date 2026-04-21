"""Witness target: loop(n) — intentionally unbounded.

M1's v1 walker does not detect unbounded loops; the expected path is
that `hand_flags.jsonl` supplies a synthetic div-zero/null-deref flag
pointing at this function so the sandbox runner is routed here. Inside
the sandbox, the wall-clock alarm (signal.alarm=10s) fires and the
child receives SIGALRM. Expected classified status:
`timeout-without-confirmation` with `signal_name: "SIGALRM"`.
"""


def loop(n):
    while True:
        n = n + 1
    return n
