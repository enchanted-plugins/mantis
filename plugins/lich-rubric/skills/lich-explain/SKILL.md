---
name: lich-explain
description: >
  Walks through why Lich flagged a specific finding — M1 flag rationale,
  M5 sandbox witness (if confirmed), M7 rubric scores per axis, Kappa
  reliability, M6 posterior state. Use when: the user runs
  /lich-explain <finding_id>, or wants to understand a HOLD/FAIL verdict.
  Do not use for: general code review (/lich-review handles that), rule
  disabling (/lich-disable handles that), or modifying rubric axes
  (rubric-v1.json edits go through a separate migration skill).
model: sonnet
tools: [Read]
---

# lich-explain

## Preconditions

- A finding exists in `plugins/lich-verdict/state/verdict.jsonl` with the given `finding_id`.
- `plugins/lich-rubric/config/rubric-v1.json` exists (defines the 5 axes).
- `plugins/lich-rubric/state/kappa-log.jsonl` has a per-axis Kappa entry for the finding's review.

## Inputs

- **Slash command**: `/lich-explain <finding_id>`

## Steps

1. **Resolve the finding.** Load `plugins/lich-verdict/state/verdict.jsonl`, find the record with matching `finding_id`.
2. **Assemble engine traces.** Read per-engine state files:
   - `plugins/lich-core/state/review-flags.jsonl` → M1 flag rationale (variable, abstract value, failure class)
   - `plugins/lich-sandbox/state/run-log.jsonl` → M5 outcome (confirmed / timeout / sandbox-error / no-bug + witness input if applicable)
   - `plugins/lich-rubric/state/kappa-log.jsonl` → M7 per-axis scores + Kappa reliability
   - `plugins/lich-preference/state/learnings.json` → M6 (developer, rule) posterior
3. **Compose explanation.** Render a structured block:
   - **Finding**: `<one-line summary>`
   - **M1 says**: abstract interpretation traced `<var>` to `<abstract value>`, flagging `<failure class>` at `<severity>`.
   - **M5 says**: witness input `<input>` produced `<outcome>`. (If confirmed: "bug reproduces"; if timeout: "alarm fired at 10s"; if no-bug: "all <N> witnesses ran clean, static flag still stands with reduced confidence".)
   - **M7 says**: per-axis scores `{clarity: 4, correctness: 3, ...}`, Kappa `{clarity: 0.72, correctness: 0.68, ...}`. Flag any axis with Kappa < 0.4 as "unstable".
   - **M6 says**: this rule's posterior for you is `Beta(α, β)` → surfacing probability `P`. (If probability hit the 5% floor, note it.)
   - **Verdict**: `DEPLOY / HOLD / FAIL`. Why this verdict (which threshold tripped).
4. **Emit to stdout**, reader-friendly formatted.

## Outputs

- stdout: human-readable explanation.
- No state writes — this is a read-only skill.

## Handoff

If the developer disagrees with the finding: suggest `/lich-disable <rule_id>`. If the developer agrees but wants to fix: the explanation already surfaces the witness input (if M5 confirmed), which is the starting point.

## Failure modes

- **F02 fabrication** — never invent a witness or abstract value. If a trace file is missing or malformed, say so: "M5 state unavailable for this finding."
- **F04 task drift** — this skill explains, it does not re-review or re-score. Don't re-run engines.
- **F13 distractor pollution** — don't dump full JSONL records into the explanation. Pick the fields that answer "why was this flagged?"

## Why Sonnet tier

Composing a reader-friendly explanation from 4-5 structured JSONL sources with honest-uncertainty framing (especially when Kappa is low or M5 timed out) benefits from Sonnet's reasoning. Haiku would oversimplify the nuance; Opus is overkill for a read-only explanation.
