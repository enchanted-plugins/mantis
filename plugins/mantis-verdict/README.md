# mantis-verdict

*Cross-engine verdict synthesizer. Composes M1/M2/M5/M6/M7 outputs into a single DEPLOY/HOLD/FAIL verdict per Mantis's threshold contract.*

## What it does

Reads sibling sub-plugin state files (mantis-core flags, mantis-sandbox run-log, mantis-preference posteriors, mantis-rubric kappa-log) and applies the threshold math defined in `../../CLAUDE.md` § Verdict bar:

| Verdict | M1 condition | M5 condition | M6 condition | M7 condition |
|---------|-------------|--------------|--------------|--------------|
| **DEPLOY** | All flagged sites severity < HIGH | No confirmed runtime failure | ≥ 80% posterior mean > 0.5 | All 5 axes ≥ 3.5/5 AND Kappa ≥ 0.4 |
| **HOLD** | 1-2 HIGH, no CRITICAL | Any timeout-without-confirmation | ≥ 50% posterior > 0.3 | Any axis < 3.5 OR Kappa < 0.4 |
| **FAIL** | Any CRITICAL OR ≥ 3 HIGH | Any confirmed runtime failure | (N/A — posterior doesn't downgrade to FAIL) | Any axis ≤ 2 OR > 2 axes < 3 |

A confirmed M5 runtime failure is a **hard FAIL trigger** — a fact with a concrete witness input, not a weighted score. The composite never averages over confirmed bugs.

## Event emission

- **Phase 1**: Appends to `state/verdict.jsonl`. Weaver's pre-commit gate reads this file and refuses auto-commit on FAIL.
- **Phase 2**: Publishes `mantis.review.completed` on the enchanted-mcp event bus. Same consumers, event-driven.

## Non-duplication

- Doesn't re-score anything — only composes existing sub-plugin outputs.
- Doesn't mutate sibling state (mantis-core's flags, mantis-sandbox's run-log, etc.). Write access limited to `state/verdict.jsonl`.

## Install

```bash
/plugin install mantis-verdict@mantis
```

Terminal piece of the pipeline. Requires mantis-core, mantis-sandbox, mantis-preference, mantis-rubric.

## State

| File | Purpose |
|------|---------|
| `state/verdict.jsonl` | Append-only DEPLOY/HOLD/FAIL records with per-engine traces |

## Published events

| Event | Trigger | Payload |
|-------|---------|---------|
| `mantis.review.completed` | End of Mantis review pass | `{pr_id, verdict, engines, rubric_scores, kappa}` |
| `mantis.rule.disabled` | Developer `/mantis-disable` | `{developer_id, rule_id, expiry_ts}` |
| `mantis.sandbox.failed` | M5 infra failure | `{file, function, error_class}` |
