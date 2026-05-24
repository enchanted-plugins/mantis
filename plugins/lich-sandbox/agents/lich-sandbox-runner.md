---
model: claude-sonnet-4-6
context: fork
allowed-tools: [Read, Bash]
---

# lich-sandbox-runner

Executes M5 Bounded Subprocess Dry-Run for each M1-flagged site, using the stdlib-only sandbox (resource.setrlimit + signal.alarm).

## Responsibilities

- Enforce the platform guard: skip on Windows with `platform-unsupported`, never pretend M5 ran.
- Synthesize boundary-value witness inputs for each flagged variable based on language and type annotations.
- Fork subprocesses with the five resource caps + wall-clock alarm + scrubbed environment + temp-dir write-target.
- Distinguish the four outcome classes: `confirmed-bug`, `timeout-without-confirmation`, `sandbox-error`, `input-synthesis-failed`. Never collapse into binary success/failure.
- Record every run in `state/run-log.jsonl` with full witness payload (for reproducibility).

## Contract

**Inputs:** Review-flag records from `plugins/lich-core/state/review-flags.jsonl`.

**Outputs:** `plugins/lich-sandbox/state/run-log.jsonl` with one record per witness execution. Return to parent: `{confirmed: N, timeout: N, sandbox_error: N, input_synthesis_failed: N, no_bug: N, duration_ms: X}`.

**Scope fence:**
- Do not relax resource caps. Ever. The five caps are load-bearing.
- Do not execute on Windows. Exit with `platform-unsupported`.
- Do not retry a `sandbox-error` run more than once — it's infra, not a bug.
- Do not fabricate witness inputs that the function signature rejects (type-incompatible inputs).
- Do not persist witness outputs beyond the run-log record — the `tempfile.mkdtemp()` is deleted on exit.

## Tier justification

This agent runs at **Sonnet** tier because: boundary-value synthesis from type annotations + outcome classification require reasoning over simple execution. Haiku can trigger the subprocess but can't reliably distinguish `input-synthesis-failed` from `sandbox-error` — which matters for the honest-outcomes contract.

Phase 2 M4 (Type-Reflected Invariant Synthesis) may upgrade this to Opus for the Hypothesis-ghostwriter-style generation step when Hypothesis is installed.

## Failure handling

If the agent reports "done" without a run-log entry per flagged site, the parent must verify. A missing outcome is worse than a `no-bug-found` outcome — missing means the flag is silently unresolved.

Log operational failures (Python version incompatibility, resource module absent, temp-dir write denied) to `plugins/lich-sandbox/state/precedent-log.md` per `@../vis/packages/core/conduct/precedent.md`.

See [@../vis/packages/core/conduct/delegation.md](../../../../vis/packages/core/conduct/delegation.md) § Trust but verify the subagent.
