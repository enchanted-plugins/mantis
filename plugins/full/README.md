# full

*Meta-plugin. One install pulls in all 7 Mantis sub-plugins via dependency resolution.*

## Install

```bash
/plugin marketplace add enchanted-plugins/mantis
/plugin install full@mantis
```

Installs the complete Mantis review pipeline:

| Sub-plugin | Engines | Role |
|-----------|---------|------|
| `mantis-core` | M1, M2 | Static analysis substrate (Cousot Interval Propagation + Falleri Structural Diff) |
| `mantis-sandbox` | M5 | Bounded Subprocess Dry-Run — confirms M1 flags with witness inputs (Unix-only) |
| `mantis-preference` | M6 | Bayesian Preference Accumulation — per-developer Beta posteriors with Thompson sampling |
| `mantis-rubric` | M7 | Zheng Pairwise Rubric Judgment — 5-axis LLM-as-judge with Kappa reliability |
| `mantis-python` | — | Language adapter mapping ruff rules into M-engine outputs |
| `mantis-typescript` | — | Language adapter mapping biome rules into M-engine outputs |
| `mantis-verdict` | — | DEPLOY/HOLD/FAIL synthesizer, event emitter |

## Cherry-pick individual sub-plugins

If you only want a subset:

```bash
/plugin install mantis-core@mantis mantis-sandbox@mantis mantis-verdict@mantis
```

Minimum viable Mantis install (no preference learning, no rubric judgment): `mantis-core + mantis-sandbox + mantis-verdict`. This gives you the static→sandbox pipeline without personalization.

## Verify

```bash
/plugin list
```

Expected output: all 7 sub-plugins + `full` listed under the `mantis` marketplace.

## Phase 2 additions (future)

The `full` meta will expand to include Phase 2 sub-plugins when they ship:
- `mantis-property-graph` (M3 Yamaguchi Property-Graph Traversal)
- `mantis-synthesis` (M4 Type-Reflected Invariant Synthesis)
- `mantis-rust`, `mantis-go`, `mantis-java`, `mantis-kotlin` (language adapters)
- `mantis-clone-detect` (Schleimer Winnowing)
- `mantis-separation-logic` (O'Hearn Bi-Abduction for JVM)
