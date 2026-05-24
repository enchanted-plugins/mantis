---
description: Walk through why Lich flagged a specific finding — M1 rationale, M5 sandbox witness, M7 rubric + Kappa, M6 posterior, and the verdict threshold that tripped. Use when the user runs /lich-explain <finding_id> or wants to understand a HOLD / FAIL.
argument-hint: <finding_id>
---

Explain why Lich flagged a specific finding. Delegates to the `lich-explain` skill (runbook at `plugins/lich-rubric/skills/lich-explain/SKILL.md`).

Argument:
- `<finding_id>` — either a numeric index into `plugins/lich-core/state/review-flags.jsonl` or a rule_id (e.g. `PY-M1-001`, `lich-python:div-zero`). The skill resolves both.

What the skill surfaces:
1. **M1** — abstract interpretation trace: variable, abstract value lattice, failure class, severity.
2. **M5** — sandbox outcome: `confirmed` (with witness input), `timeout` (alarm at 10s), `sandbox-error`, or `no-bug` (static flag downgraded). Windows platforms report `platform-unsupported`.
3. **M7** — per-axis rubric scores, Cohen's Kappa per axis. Axes with Kappa < 0.4 are surfaced as "unstable" — never collapsed into a hidden average (honest-numbers contract).
4. **M6** — (developer, rule) Beta posterior + Thompson surfacing probability. The 5% floor is noted when it applies.
5. **Verdict** — which threshold tripped (DEPLOY / HOLD / FAIL per the root `CLAUDE.md` § Verdict bar).

Read-only — this command never re-runs engines, never re-scores, never modifies state. If the developer disagrees with the finding, handoff is `/lich-disable <rule_id>`.
