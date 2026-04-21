# mantis-sandbox — Precedent Log

Self-observed operational failures for the M5 Bounded Subprocess Dry-Run engine. Format per `shared/conduct/precedent.md`. Append; never delete without marking `RESOLVED YYYY-MM-DD`.

Consult: grep before editing `limits.py`, `sandbox.py`, or anything that crosses the Windows/WSL boundary.

---

## 2026-04-21 — Windows host has no `resource.setrlimit`

**Command that failed:**
`python plugins/mantis-sandbox/scripts/sandbox.py` on a native Windows interpreter — `import resource` fails with `ModuleNotFoundError`.

**Why it failed:**
`resource.setrlimit` is POSIX-only. The Python stdlib does not ship it on Windows. Any call site that assumes it can `import resource` at module-load time crashes before the caps can be applied.

**What worked:**
Emit `platform-unsupported` as a first-class sandbox outcome per `CLAUDE.md § behavioral contract 2`. Windows path: gate the `resource` import, skip M5, log a verdict annotation. Never silently pretend M5 ran.

**Signal:** code that crosses the `wsl.exe` boundary cannot `import` from `limits.py` — the caps live on the Linux side. Duplicate the five cap constants (CPU=5s, AS=512MB, NOFILE=16, FSIZE=10MB, alarm=10s) at the boundary; do not try to share the module.

**Tags:** windows, wsl, resource, rlimit, platform-unsupported, m5
