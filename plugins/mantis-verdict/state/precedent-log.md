# mantis-verdict — Precedent Log

Self-observed operational failures for the verdict composer. Format per `shared/conduct/precedent.md`. Append; never delete without marking `RESOLVED YYYY-MM-DD`.

Consult: grep before adding or modifying PostToolUse / Stop hook dispatch branches.

---

## 2026-04-21 — Verdict-compose fired on non-Python Writes

**Command that failed:**
`shared/hooks/dispatch.sh` `mantis-verdict-compose` branch dispatched on any Write/Edit matcher. A `.md` or `.json` Write composed a preliminary `DEPLOY` verdict and appended a noisy row to `verdict.jsonl`.

**Why it failed:**
The compose branch lacked a `_is_python_file` gate. Write/Edit events matched unconditionally, so doc-only changes triggered compose even though M1/M2/M5 never ran on them — empty-evidence DEPLOY.

**What worked:**
Added `_is_python_file "$FILE_PATH"` gate before the compose call. Stop-event invocations (empty `FILE_PATH`) still dispatch by design — those are end-of-session syntheses, not per-edit. Regression coverage: `tests/regression/test_bugs_2026_04_21.sh` bug-2.

**Signal:** any hook spawn must gate by file type (or explicitly allow empty `FILE_PATH` for Stop events) before firing, to avoid state-mutation noise. A `verdict.jsonl` row per doc edit is the canonical tell.

**Tags:** bash, hook, dispatch, file-gate, verdict, stop-event
