---
name: lich-python
description: >
  Python language adapter. When the target file is .py, maps the ruff rule
  catalog (~900 rules) into M-engine outputs and contributes Python-specific
  idiom checks (pyupgrade, typing, async-await patterns). Use when:
  lich-core fires on a .py file. Do not use for: non-Python files (lich-
  typescript covers .ts/.tsx; other languages are Phase 2), or as a
  replacement for ruff itself — this skill maps ruff outputs into Lich's
  M-engine vocabulary, it does not re-implement ruff.
model: haiku
tools: [Read, Bash]
---

# lich-python

## Preconditions

- Target file extension is `.py`.
- `config/ruff-rule-map.json` exists (ships with the adapter; maps ruff rule IDs to M-engine severity + category).
- Optional: `ruff` is installed in the developer's environment for the richer invocation path. If absent, fall back to Python stdlib `ast`-based idiom checks using the rule-map's stdlib-feasible subset (~40 of ~120 rules).

## Inputs

- **Chained from lich-core**: `{file: "foo.py", ast_tree: <parsed>}`

## Steps

1. **Detect ruff availability.** Run `ruff --version` via subprocess. Record the version for version-drift detection.
2. **Invoke ruff (if available).** `ruff check --output-format=json <file>` — parse the JSON output.
3. **Fall back (if ruff absent).** Run the stdlib-feasible rule subset directly over the `ast_tree` from lich-core.
4. **Map rule IDs to M-engine categories.** For each finding, load `config/ruff-rule-map.json` and emit:
   - `M1 runtime-failure candidate` — if rule is in the "correctness" category (E501 exceptions, F401 unused imports that may cause NameError, etc.)
   - `M7 idiom suggestion` — if rule is in the "style/idiom" category (UP-series pyupgrade, SIM simplify)
   - `skip` — if rule is in the "Hydra-overlap" category (CWE-tagged security rules — these belong to Hydra R3)
5. **Emit findings.** Append to `plugins/lich-core/state/review-flags.jsonl` under the lich-core sub-plugin's collection point.

## Outputs

- Appends to `plugins/lich-core/state/review-flags.jsonl`.
- Return value: `{ruff_version: "...", rules_fired: N, skipped_hydra_overlap: N}`.

## Handoff

Findings flow back through lich-core → lich-sandbox (for M1-class flags) → lich-rubric → lich-verdict.

## Failure modes

- **F14 version drift** — ruff's JSON output format changes; the adapter must pin a tested range and warn on mismatch.
- **F04 task drift** — re-implementing ruff rules in the adapter. Don't. The adapter *maps*; ruff does the work.
- **F07 over-helpful substitution** — running ruff with `--fix` and auto-rewriting code. Never. Lich is advisory.

## Why Haiku tier

This skill is a thin mapping layer: invoke subprocess, parse JSON, lookup in rule-map, emit. No reasoning. Haiku is appropriate.
