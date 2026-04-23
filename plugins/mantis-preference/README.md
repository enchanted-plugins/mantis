# lich-preference

*M6 Bayesian Preference Accumulation. Per-(developer, rule) Beta-Binomial posteriors with Thompson sampling and a 5% minimum surfacing floor.*

## What it does

Learns each developer's rule preferences from their accept/reject signals, without over-fitting to any single rejection. The math:

- Every `(developer_id, rule_id)` pair has a Beta posterior `Beta(α, β)` starting at `Beta(1, 1)` (uniform).
- **Accept → α++.** Reject → β++. Override (developer ignored without explicit decision) → split update: `α += 0.5`, `β += 0.5`.
- On each review, Lich **Thompson-samples** a surfacing probability from the posterior for every candidate rule. High-mean rules surface often; high-variance rules get exploration.
- **5% minimum floor.** No rule dies permanently from accumulated rejections. Permanent suppression requires the developer's explicit `/lich-disable <rule>`.

## Why this matters

No shipped reviewer does this today. GitHub Copilot uses static markdown rule files. Cursor's "Memories" are auto-generated notes, not a posterior. Qodo Merge accumulates team-level rules, not per-developer. JetBrains Mellum re-ranks completions by accept history but doesn't expose rule-level preference.

M6 is genuinely novel — the first principled per-developer Bayesian posterior over review rules, with uncertainty-aware sampling. That honest-uncertainty property is load-bearing: one rejection shouldn't kill a rule, and the posterior tells you how many signals you have before trusting the mean.

## Non-duplication

- Does not override Crow's trust score — consumes it as a prior *multiplier*, not a replacement.
- Does not make security findings "preferable" or "unpreferable" — Hydra R3's lane is out of scope.

## Install

```bash
/plugin install lich-preference@lich
```

## Skills

| Skill | Purpose |
|-------|---------|
| `/lich-disable <rule_id>` | Permanently suppress a rule (90-day expiry; renewable) |

Ambient accept/reject learning happens automatically via the PostToolUse hook — no developer command needed.

## State

| File | Purpose |
|------|---------|
| `state/learnings.json` | Per-(developer, rule) Beta(α, β) posteriors |
| `state/overrides.json` | Developer-disabled rules with quarterly re-prompt dates |

## Schema

`state/learnings.json`:

```json
{
  "schema_version": "1.0",
  "developer_id": "git-email-sha256-12char",
  "repo_id": "path-sha256-12char",
  "last_update": "2026-04-20T...",
  "rules": {
    "lich-python:unused-import": {
      "alpha": 3, "beta": 7,
      "surface_count": 10, "accept_count": 2,
      "reject_count": 6, "override_count": 2,
      "last_update": "2026-04-20T..."
    }
  }
}
```

## Source

- [Thompson 1933 — original Beta-Binomial paper](https://www.jstor.org/stable/2332286)
- [Russo & Van Roy 2018 — A Tutorial on Thompson Sampling (Stanford)](https://web.stanford.edu/~bvr/pubs/TS_Tutorial.pdf)
