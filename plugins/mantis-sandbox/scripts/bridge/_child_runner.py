"""Mantis M5 sandbox — WSL-side child runner.

This file is the *source* of the script body that `wsl.py` pipes into
`wsl.exe -e python3` via stdin. It is designed to be:

* Self-contained — the script crosses the `wsl.exe` process boundary as a
  string; it cannot `import` other modules in this package.
* POSIX-only at runtime — applies `resource.setrlimit` + `signal.alarm`
  inside WSL's Linux Python. The file imports cleanly on Windows because
  the POSIX-only imports are done inside `_main`, not at module scope.
* Byte-identical in behaviour to Agent 3's POSIX runner child template:
  loads the target via `importlib.util`, invokes the function with
  witness args/kwargs, prints result JSON to stdout, prints traceback to
  stderr on exception, exits non-zero.

The 6 resource-cap constants below are DUPLICATED from
`plugins/mantis-sandbox/scripts/limits.py`. This is the one place the
duplication is tolerated: the child runs across the `wsl.exe` boundary
in a different Python interpreter (Linux), so it cannot `from .. import
limits`. The duplication is load-bearing — if these drift from
`limits.py`, the WSL path silently relaxes the sandbox. Any edit to
`limits.py` MUST be mirrored here and vice versa.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Load-bearing constants — MUST match plugins/mantis-sandbox/scripts/limits.py
# ---------------------------------------------------------------------------
CAP_CPU_S = 5
CAP_AS_BYTES = 512 * 1024 * 1024
CAP_NOFILE = 16
CAP_FSIZE_BYTES = 10 * 1024 * 1024
CAP_NPROC = 0
ALARM_S = 10


# The script body piped into `wsl.exe -e python3 -`. Kept as a single
# string so `wsl.py` can concatenate it with no filesystem touch on the
# Linux side — the child has no on-disk footprint inside WSL.
#
# Contract with wsl.py: stdin carries a JSON document
#   {"target_file": "/mnt/c/...", "function_name": "...",
#    "witness": {"args": [...], "kwargs": {...}}}
# Stdout carries a single JSON line on clean return; stderr carries the
# traceback on exception. Exit codes mirror Agent 3's POSIX runner:
#   0 = clean; 1 = function raised; 2 = witness parse error;
#   3 = target load failure; 4 = function not found;
#   5 = import/setup exception.
CHILD_SCRIPT = '''\
import importlib.util, json, sys, traceback

# --- Load-bearing cap constants (mirror of limits.py) ----------------------
CAP_CPU_S = {CAP_CPU_S}
CAP_AS_BYTES = {CAP_AS_BYTES}
CAP_NOFILE = {CAP_NOFILE}
CAP_FSIZE_BYTES = {CAP_FSIZE_BYTES}
CAP_NPROC = {CAP_NPROC}
ALARM_S = {ALARM_S}


def _apply_caps():
    """Install the 6 caps + alarm inside this WSL Python process."""
    import resource, signal
    resource.setrlimit(resource.RLIMIT_CPU, (CAP_CPU_S, CAP_CPU_S))
    resource.setrlimit(resource.RLIMIT_AS, (CAP_AS_BYTES, CAP_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (CAP_NOFILE, CAP_NOFILE))
    resource.setrlimit(resource.RLIMIT_FSIZE, (CAP_FSIZE_BYTES, CAP_FSIZE_BYTES))
    nproc = getattr(resource, "RLIMIT_NPROC", None)
    if nproc is not None:
        try:
            resource.setrlimit(nproc, (CAP_NPROC, CAP_NPROC))
        except (ValueError, OSError):
            pass
    signal.alarm(ALARM_S)


try:
    payload = json.loads(sys.stdin.read())
    target_file = payload["target_file"]
    function_name = payload["function_name"]
    witness = payload.get("witness", {{}})
    args = witness.get("args", [])
    kwargs = witness.get("kwargs", {{}})
except Exception:
    traceback.print_exc()
    sys.exit(2)

try:
    _apply_caps()
except Exception:
    # Cap application failed — this is sandbox-error territory, not a finding.
    # Exit 5 so outcome.classify routes to sandbox-error rather than confirmed-bug.
    traceback.print_exc()
    sys.exit(5)

try:
    spec = importlib.util.spec_from_file_location("sandbox_target", target_file)
    if spec is None or spec.loader is None:
        print("sandbox: could not load target", file=sys.stderr)
        sys.exit(3)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fn = getattr(mod, function_name, None)
    if fn is None:
        print("sandbox: no function " + repr(function_name) + " in target", file=sys.stderr)
        sys.exit(4)
except Exception:
    traceback.print_exc()
    sys.exit(5)

try:
    result = fn(*args, **kwargs)
    try:
        print(json.dumps({{"ok": True, "result": repr(result)}}))
    except Exception:
        print(json.dumps({{"ok": True, "result": "<unprintable>"}}))
    sys.exit(0)
except BaseException:
    # BaseException catches SystemExit/KeyboardInterrupt/MemoryError too;
    # the error-class tag in the traceback is what outcome.classify reads.
    traceback.print_exc()
    sys.exit(1)
'''.format(
    CAP_CPU_S=CAP_CPU_S,
    CAP_AS_BYTES=CAP_AS_BYTES,
    CAP_NOFILE=CAP_NOFILE,
    CAP_FSIZE_BYTES=CAP_FSIZE_BYTES,
    CAP_NPROC=CAP_NPROC,
    ALARM_S=ALARM_S,
)
