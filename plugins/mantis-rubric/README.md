# lich-rubric

*M7 Zheng Pairwise Rubric Judgment. 5-axis LLM-as-judge with position-swap debiasing and Cohen's Kappa inter-judge reliability.*

## What it does

Catches the style / clarity / idiom signals no static engine can — the "is this code *good* to read?" dimension — without hand-waving the uncertainty. For every diff:

1. Judge scores on 5 orthogonal axes (Clarity, Correctness-at-glance, Idiom-fit, Testability, Simplicity), scale 1-5 per axis.
2. Judge runs twice with diff orderings swapped (before→after, then after→before).
3. Per-axis Cohen's Kappa computed; axes with Kappa < 0.4 flagged "unstable" in the PDF report — never collapsed into a hidden average.
4. If any axis shows |score delta| ≥ 1.5 between runs → escalate that axis to Opus adjudicator.

## The 5 axes

| Axis | Definition |
|------|-----------|
| Clarity | Can a reviewer summarize this function's purpose in one sentence after 30 seconds? |
| Correctness-at-glance | Are guards (null checks, boundary checks, error handling) visible without tracing? |
| Idiom-fit | Does the code match this language's conventions and this repo's patterns? |
| Testability | Are there seams for unit tests without mocking the universe? |
| Simplicity | Is the solution the simplest that solves the stated problem? |

## Model tiering

| Tier | When | Cost contract |
|------|------|---------------|
| Sonnet (default) | Normal reviews | Base cost |
| Haiku | Downshift when Pech's `pech.budget.threshold.crossed` fires at 80% | Respect budget |
| Opus | Disagreement adjudication only (axis with \|delta\| ≥ 1.5 between two Sonnet runs) | Reserved for judgment |

## Non-duplication

- Not a security reviewer (Hydra R3).
- Not a change classifier (Crow V1/V2).
- Not a correctness prover (M1+M5 handle that — M7 judges readability and idiom, not soundness).

## Install

```bash
/plugin install lich-rubric@lich
```

## Skills

| Skill | Purpose |
|-------|---------|
| `/lich-explain <finding_id>` | Walk through why M1/M5/M7 flagged a specific finding, with honest Kappa reporting |

## State

| File | Purpose |
|------|---------|
| `config/rubric-v1.json` | Versioned rubric (axes + definitions + debiasing config + judge tiers) — ship-time, committed |
| `state/kappa-log.jsonl` | Per-axis Cohen's Kappa history across reviews |

## Source

- [MT-Bench — Zheng et al. 2023 (arXiv:2306.05685)](https://arxiv.org/abs/2306.05685): position bias, self-preference bias, Cohen's Kappa for LLM-as-judge.
- Recursive Rubric Decomposition 2025: 5-8 orthogonal axes reduce variance vs. monolithic judge.
