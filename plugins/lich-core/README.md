# lich-core

*M1 Cousot Interval Propagation + M2 Falleri Structural Diff. The static-analysis substrate of Lich.*

## What it does

Runs two engines on every code change:

1. **M1 Cousot Interval Propagation** — abstract interpretation over interval, nullability, and container-shape lattices. Flags runtime-failure candidates: division-by-zero, null/None deref, array OOB, integer overflow, resource leak. Widening terminates at N=3 iterations using language-aware thresholds.
2. **M2 Falleri Structural Diff** — GumTree two-phase AST matching (top-down hash + bottom-up Dice). Isolates semantic edits from formatting churn so reviewer attention lands on what matters.

Flags queue for **lich-sandbox** M5 confirmation; outputs feed **lich-verdict** for DEPLOY/HOLD/FAIL composition.

## Non-duplication

- **Never re-scans for CWE-tagged security findings.** Hydra's R3 OWASP Vulnerability Graph owns that. If Hydra flagged the file, lich-core boosts M6 attention weight and annotates M7 rubric input — does not re-classify.
- **Never re-classifies changes.** Crow's V1 Semantic Diff + V2 Bayesian Trust are authoritative. lich-core consumes Crow's trust score into M6 priors; does not peer-classify.

## Install

```bash
/plugin install lich-core@lich
```

Or install all 7 Lich sub-plugins at once:

```bash
/plugin install full@lich
```

## Skills

| Skill | Purpose |
|-------|---------|
| `/lich-review <scope>` | On-demand M1 + M2 pass on a hunk, file, or PR |

## State

| File | Purpose |
|------|---------|
| `state/learnings.json` | Per-session learnings for Gauss Accumulation (M1 threshold tuning, M2 parameter tuning) |
| `state/precedent-log.md` | Self-observed operational failures |
| `state/review-flags.jsonl` | Append-only flag records consumed by lich-sandbox |

## Source

- M1: [Cousot & Cousot POPL'77 — Abstract Interpretation](https://www.di.ens.fr/~cousot/COUSOTpapers/publications.www/CousotCousot-POPL-77-ACM-p238--252-1977.pdf)
- M2: [Falleri et al. ASE'14 — Fine-grained and Accurate Source Code Differencing (GumTree)](https://hal.science/hal-01054552/document)
