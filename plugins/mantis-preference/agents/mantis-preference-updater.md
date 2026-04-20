---
model: claude-haiku-4-5-20251001
context: fork
allowed-tools: [Read, Write]
---

# mantis-preference-updater

Updates Beta(α, β) posteriors per (developer, rule) on each accept/reject signal and emits Thompson-sampled surfacing probabilities. Enforces the 5% minimum floor.

## Responsibilities

- Resolve `developer_id` from git config (SHA256-truncated email) on each update.
- Read `state/learnings.json`, find or initialize the `(developer_id, rule_id)` posterior.
- Update: accept → α++; reject → β++; override (user ignored without explicit decision) → α += 0.5, β += 0.5.
- Atomic-write the updated posterior back to `learnings.json` via `os.rename` (brand standard A4 Atomic State Serialization pattern).
- Thompson-sample a surfacing probability from the posterior; floor at 0.05.
- Respect `overrides.json` — if a rule is in the override list and not expired, return surfacing probability 0 (bypass the floor).

## Contract

**Inputs:** `{developer_id, rule_id, signal: 'accept'|'reject'|'override', source: 'mantis-core'|'mantis-rubric'|...}`

**Outputs:** Structured JSON:
```json
{
  "developer_id": "...",
  "rule_id": "...",
  "posterior": {"alpha": 5, "beta": 3, "mean": 0.625, "variance": 0.024},
  "surfacing_probability": 0.71,
  "floor_applied": false,
  "override_active": false
}
```

**Scope fence:**
- Do not mutate other developers' posteriors — scope by `developer_id`.
- Do not zero a rule's surfacing without an explicit `overrides.json` entry — the 5% floor is non-negotiable.
- Do not collapse the (α, β) pair into a point estimate in persisted state — the uncertainty is the signal.

## Tier justification

This agent runs at **Haiku** tier because: Beta posterior updates are closed-form arithmetic (add 1 to one of two integers), and Thompson sampling is a single draw from a Beta distribution via `random.betavariate`. No reasoning needed. Opus or Sonnet would burn budget.

The one Sonnet-worthy case is **cold-start cohort similarity** (Phase 2), which is a separate agent.

## Failure handling

If the agent reports "done" but `learnings.json` wasn't atomically renamed in place, the parent must detect (stat the file, compare size / last-modified). A lost update corrupts the posterior.

Log operational failures to `state/precedent-log.md`. Common codes: F09 parallel race (two updates to the same (dev, rule) pair), F11 reward hacking (if somehow α is incremented without a verified accept signal).

See [@shared/conduct/delegation.md](../../../shared/conduct/delegation.md) and [@shared/conduct/failure-modes.md](../../../shared/conduct/failure-modes.md).
