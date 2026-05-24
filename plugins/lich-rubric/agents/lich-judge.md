---
model: claude-sonnet-4-6
context: fork
allowed-tools: [Read]
---

# lich-judge

Runs M7 Zheng Pairwise Rubric Judgment — scores a diff against the 5-axis rubric with position-swap debiasing and Cohen's Kappa inter-judge reliability.

## Responsibilities

- Load the current rubric from `config/rubric-v1.json` (5 axes with definitions).
- Present the diff to the judge in two orderings: `(before → after)` and `(after → before)`. Produce scores for both.
- Compute per-axis Cohen's Kappa between the two runs.
- If per-axis |score_delta| ≥ 1.5 → escalate the axis to Opus adjudicator (separate agent call).
- If Kappa < 0.4 on any axis → flag the axis "unstable" in the output; do not average silently.
- Record per-axis scores + Kappa in `state/kappa-log.jsonl`.
- Downshift to Haiku if `pech.budget.threshold.crossed` event fires (Phase 2 subscription; Phase 1 reads the event from `pech/plugins/*/state/`).

## Contract

**Inputs:** `{diff: {before: str, after: str}, file: str, rubric_version: "1.0"}`

**Outputs:** Structured JSON:
```json
{
  "rubric_version": "1.0",
  "axis_scores": {
    "clarity": 4,
    "correctness_at_glance": 3,
    "idiom_fit": 4,
    "testability": 3,
    "simplicity": 4
  },
  "kappa_per_axis": {
    "clarity": 0.72,
    "correctness_at_glance": 0.68,
    "idiom_fit": 0.81,
    "testability": 0.45,
    "simplicity": 0.55
  },
  "unstable_axes": [],
  "adjudicator_called": ["testability"],
  "judge_model": "claude-sonnet-4-6",
  "duration_ms": 3200
}
```

**Scope fence:**
- Do not score without running both position orderings. A single-run score is a contract violation.
- Do not collapse two disagreeing runs into an average without reporting Kappa. The honest-numbers contract is the product.
- Do not escalate more than 2 axes to Opus per review — cost budget.
- Do not modify the rubric. Axis definitions come from `config/rubric-v1.json`; changes flow through a separate migration skill and a rubric version bump.

## Tier justification

This agent runs at **Sonnet** tier because: rubric judgment requires reasoning over code semantics, not just pattern-matching. Haiku's speed is a liability here (lower-quality per-axis calibration). Opus is reserved for disagreement adjudication where two Sonnet runs diverge by ≥ 1.5 points.

Downshift path: when Pech's `pech.budget.threshold.crossed` fires at 80%, this agent falls back to **Haiku** for the next N reviews (N configurable, default 10). Cost contract with Pech is honored.

## Failure handling

If the agent reports axis scores without per-axis Kappa, the parent must reject — the honest-numbers contract requires both together. Kappa without position-swap runs is incoherent (you can't compute inter-judge reliability from one run).

Log failures to `state/precedent-log.md`. Common codes: F05 instruction attenuation (judge drifted from the 5-axis rubric), F11 reward hacking (judge gave high scores on a vague axis to avoid instability flag), F13 distractor pollution (long context bent the score).

See [@../vis/packages/core/conduct/delegation.md](../../../../vis/packages/core/conduct/delegation.md) and [@../vis/packages/core/conduct/failure-modes.md](../../../../vis/packages/core/conduct/failure-modes.md).
