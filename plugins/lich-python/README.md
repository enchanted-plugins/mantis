# lich-python

*Python language adapter for Lich. Maps ruff rule IDs into M-engine outputs.*

## What it does

Runs when the target file is `.py`. Invokes ruff (astral-sh/ruff) if installed and parses its JSON output; falls back to a stdlib-`ast`-based subset (~40 of ~120 mapped rules) if ruff isn't available.

Maps each ruff finding to an M-engine category via `config/ruff-rule-map.json`:

| Ruff category | Lich M-engine route |
|---------------|----------------------|
| F-series (pyflakes — unused, undefined, redefined) | M1 runtime-failure candidate |
| UP-series (pyupgrade) | M7 rubric — Idiom-fit axis |
| SIM-series (simplify) | M7 rubric — Idiom-fit axis |
| C901 / PLR cyclomatic | M7 rubric — Simplicity axis |
| N-series (naming) | M7 rubric — Clarity axis |
| S-series (bandit security) | **Skip** — Hydra R3 owns these |

## Launch-to-full coverage

- At launch: ~120 of ruff's ~900 rules mapped.
- Phase 2: expand coverage based on real review data — which rules developers care about, which fire-and-get-ignored.
- Security (S-series) intentionally excluded forever — Hydra owns the CWE lane.

## Non-duplication

- Never maps S-series rules (Hydra R3 overlap).
- Never runs with `ruff --fix` — Lich is advisory.
- Never replaces ruff — this is an adapter, not a re-implementation.

## Install

```bash
/plugin install lich-python@lich
```

Requires lich-core. Optional: `ruff` installed in the developer's environment for richer output.

## State

| File | Purpose |
|------|---------|
| `config/ruff-rule-map.json` | Ruff rule ID → M-engine category mapping (ship-time, committed) |
| `state/` | Runtime-only dir (empty at launch; plugin code may write ad-hoc cache) |

## Source

- [ruff (astral-sh)](https://github.com/astral-sh/ruff) — Rust-rewritten Python linter, ~10-100× faster than flake8.
- [ruff rule index](https://docs.astral.sh/ruff/rules/) — all ~900 rules with categories.
