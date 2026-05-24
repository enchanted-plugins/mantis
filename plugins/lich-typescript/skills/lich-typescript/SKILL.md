---
name: lich-typescript
description: >
  TypeScript / JavaScript language adapter. When target file is .ts, .tsx,
  .js, or .jsx, invokes biome (if installed) or tsc --noEmit (substrate
  fallback), maps findings into M-engine outputs, and contributes TS-
  specific idiom checks (React hooks deps, JSX a11y, narrow-type-guards).
  Use when: lich-core fires on a TypeScript/JavaScript file. Do not use
  for: non-TS/JS files; replacing biome; auto-fix mode (Lich is advisory).
model: haiku
tools: [Read, Bash]
---

# lich-typescript

## Preconditions

- Target file extension is `.ts`, `.tsx`, `.js`, `.jsx`, or `.mjs`.
- `config/biome-rule-map.json` exists.
- One of these is available in the repo: `biome` binary, `tsc --noEmit` via local `typescript` devDep, or the Lich fallback (minimal regex-based idiom checks — last resort).

## Inputs

- **Chained from lich-core**: `{file: "Foo.tsx", substrate_status: "ok"|"parse-failed"}`

## Steps

1. **Detect biome availability.** Try `biome --version`. If present, invoke `biome check --json --reporter=json <file>`.
2. **Fall back to `tsc --noEmit`** if biome absent but TypeScript is installed. Parse diagnostic output.
3. **Final fallback: stdlib.** If neither biome nor tsc is available, run the regex-based idiom subset (~20 rules) and flag `substrate: fallback-regex` in the output — honest about reduced coverage.
4. **Map rule IDs to M-engine categories.** From `config/biome-rule-map.json`:
   - `M1 runtime-failure candidate` — correctness rules (no-unused-vars, no-undef, strict-null-checks violations)
   - `M7 idiom suggestion` — style rules (useExhaustiveDependencies, useArrowFunction, useConst)
   - `skip` — Hydra-overlap rules (noSecrets, noEval, noUnsafeOptionalChaining with security framing)
5. **Emit findings.** Append to `plugins/lich-core/state/review-flags.jsonl`.

## Outputs

- Appends to `plugins/lich-core/state/review-flags.jsonl`.
- Return: `{substrate: "biome"|"tsc"|"fallback-regex", rules_fired: N, skipped_hydra_overlap: N}`.

## Handoff

Findings flow through the standard lich pipeline: lich-core → lich-sandbox (M1-class) → lich-rubric → lich-verdict.

## Failure modes

- **F14 version drift** — biome JSON output or tsc diagnostic format changes. Pin tested version range; warn on mismatch.
- **F04 task drift** — re-implementing biome rules. Don't; the adapter maps, biome does the work.
- **F07 over-helpful substitution** — `biome check --apply`. Never. Lich is advisory.
- **F13 distractor pollution** — on a 10k-line file, biome may emit hundreds of findings; filter to changed hunks (from Crow's change classification) before mapping.

## Why Haiku tier

Thin mapping layer — invoke subprocess, parse JSON, lookup, emit. No reasoning.
