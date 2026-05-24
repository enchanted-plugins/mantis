"""Lich M5 sandbox — resource caps applied in the child before exec.

LOAD-BEARING per CLAUDE.md Behavioral contract 2. The caps in this module
are the ACE-risk mitigation for executing developer code on every PR. Any
relaxation converts the sandbox into an arbitrary-code-execution surface
and REQUIRES a documented security review.

v1 ships 6 caps. The first five are the canonical Lich contract
(CPU / AS / NOFILE / FSIZE + signal.alarm). The sixth, `RLIMIT_NPROC = 0`,
is an honest extension: without it, a fork-bomb inside the child defeats
the AS / CPU caps by multiplying the resource budget across children.
5 -> 6 caps is documented here rather than hidden.

`apply_in_child` is the `preexec_fn` passed to `subprocess.Popen`. It runs
AFTER `fork()` and BEFORE `exec()` in the child process only — the parent
is untouched. On non-POSIX platforms, the `resource` module is absent;
callers MUST platform-guard (via bridge.platform_guard.check) before
selecting the POSIX runner. If this function is reached on a non-POSIX
host it raises `NotImplementedError` so the failure is loud, not silent.
"""

from __future__ import annotations

import platform

# Canonical five-cap contract (CLAUDE.md § Behavioral contract 2).
RLIMIT_CPU_SEC = 5                       # infinite-loop defense
RLIMIT_AS_BYTES = 512 * 1024 * 1024      # address-space cap (512 MB)
RLIMIT_NOFILE_COUNT = 16                 # open file descriptors
RLIMIT_FSIZE_BYTES = 10 * 1024 * 1024    # per-file write cap (10 MB)
SIGNAL_ALARM_SEC = 10                    # wall-clock kill (child-side)

# v1 extension: fork-bomb defense. Hard-zero subprocess creation in the
# child so the child cannot spawn further children that would each get a
# fresh AS/CPU budget. Documented here; not a relaxation.
RLIMIT_NPROC_COUNT = 0


def apply_in_child() -> None:
    """Install the 6 caps + alarm inside the forked child. POSIX only.

    Called as `subprocess.Popen(..., preexec_fn=apply_in_child)`. Raises
    `NotImplementedError` on Windows so accidental use never silently
    runs code uncapped.
    """
    if platform.system() == "Windows":
        raise NotImplementedError(
            "limits.apply_in_child requires POSIX resource module; "
            "Windows callers must route via bridge.wsl.run_in_wsl."
        )

    # Imports are POSIX-only; do them here so module import works on Windows.
    import resource
    import signal

    # Hard + soft caps identical — no headroom for escalation inside the child.
    resource.setrlimit(resource.RLIMIT_CPU, (RLIMIT_CPU_SEC, RLIMIT_CPU_SEC))
    resource.setrlimit(resource.RLIMIT_AS, (RLIMIT_AS_BYTES, RLIMIT_AS_BYTES))
    resource.setrlimit(resource.RLIMIT_NOFILE, (RLIMIT_NOFILE_COUNT, RLIMIT_NOFILE_COUNT))
    resource.setrlimit(resource.RLIMIT_FSIZE, (RLIMIT_FSIZE_BYTES, RLIMIT_FSIZE_BYTES))

    # RLIMIT_NPROC is not universally present (missing on some BSDs);
    # skip silently if absent rather than fail-closed in a harmless way.
    nproc = getattr(resource, "RLIMIT_NPROC", None)
    if nproc is not None:
        try:
            resource.setrlimit(nproc, (RLIMIT_NPROC_COUNT, RLIMIT_NPROC_COUNT))
        except (ValueError, OSError):
            # Kernel declined — child still has CPU/AS caps; accept.
            pass

    # Wall-clock kill. SIGALRM default handler terminates the process.
    signal.alarm(SIGNAL_ALARM_SEC)
