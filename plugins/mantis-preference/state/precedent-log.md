# mantis-preference — Precedent Log

Self-observed operational failures for the M6 Bayesian Preference Accumulation engine. Format per `shared/conduct/precedent.md`. Append; never delete without marking `RESOLVED YYYY-MM-DD`.

Consult: grep before touching any `_HERE.parents[N]` repo-root calculation in preference scripts.

---

## 2026-04-21 — `_HERE.parents[3]` overshot repo root

**Command that failed:**
`python plugins/mantis-preference/scripts/override.py` — default `overrides.json` resolved to `enchanted-skills/overrides.json` instead of `enchanted-skills/mantis/plugins/mantis-preference/state/overrides.json`.

**Why it failed:**
From `plugins/mantis-preference/scripts/override.py`, `Path(__file__).parents[3]` climbs: `scripts` → `mantis-preference` → `plugins` → `mantis` → **`enchanted-skills`** (one too far). The sibling idiom (sandbox.py, compose.py) uses `parents[2]` and lands on repo root correctly.

**What worked:**
Switched to `_HERE.parents[2]`. Regression coverage: `tests/regression/test_bugs_2026_04_21.sh` bug-3.

**Signal:** match the `parents[2]` idiom used in `sandbox.py` + `compose.py`; verify by checking `(ROOT / ".git").exists()` at the computed root before trusting it. Any default state path that lands outside the repo is the canonical tell.

**Tags:** pathlib, parents, repo-root, m6, state-path
