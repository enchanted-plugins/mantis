# docs/assets — rendered diagrams & equations

Pre-rendered SVGs so GitHub's mobile app (which renders neither
` ```mermaid ` blocks nor `$$...$$` math) shows them correctly. The
root `README.md` references the files here as `<img>`.

## Files

| File | Source | Regenerate |
|------|--------|-----------|
| `pipeline.svg` | `pipeline.mmd` | `npx -y @mermaid-js/mermaid-cli -i pipeline.mmd -o pipeline.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js pipeline.svg` |
| `lifecycle.svg` | `lifecycle.mmd` | `npx -y @mermaid-js/mermaid-cli -i lifecycle.mmd -o lifecycle.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js lifecycle.svg` |
| `state-flow.svg` | `state-flow.mmd` | `npx -y @mermaid-js/mermaid-cli -i state-flow.mmd -o state-flow.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js state-flow.svg` |
| `math/*.svg` | `render-math.js` | `npm install --prefix . mathjax-full && node render-math.js` |

Run the commands from `docs/assets/` (paths are relative). The
toolchain (`node_modules/`, `package-lock.json`) is gitignored; only
the rendered SVGs, their `.mmd` sources, and the configs committed
above are tracked.

The `apply-blueprint.js` step overlays an engineering-blueprint grid
(navy `#0a1628` paper, `#1e3a5f` major lines / `#16304f` minor lines)
onto the rendered diagram so it reads as a CAD drawing rather than a
neutral dark card. This is the shared visual identity across every
sibling repo (emu, wixie, crow, pech, hydra, sylph, lich).

## Diagram content

- **pipeline.svg** (MNT-001) — seven-sub-plugin architecture. Crow
  change-classification input; lich-core (M1 + M2) static-suspicion
  layer; lich-sandbox (M5) runtime confirmation; lich-preference +
  lich-rubric (M6 + M7) preference-filter and judgment layers;
  lich-python + lich-typescript language adapters; lich-verdict
  cross-engine DEPLOY/HOLD/FAIL router; peer-plugin subscription
  legend (Sylph gates merge, Pech attributes spend, Hydra keeps CWE
  exclusivity).

- **lifecycle.svg** (MNT-002) — five-stage review lifecycle:
  PostToolUse intake → sandbox dry-run (on M1 flags) → Bayesian
  preference filter → pairwise rubric judgment → verdict synthesis at
  end-of-PR. Orthogonal branch: developer-invoked `/lich-review`,
  `/lich-explain`, `/lich-disable` commands.

- **math/** — 5 named-engine equations rendered from `render-math.js`:
  `m1-interval.svg` (Cousot interval + nullability + shape lattice
  with widening), `m2-ast-diff.svg` (GumTree two-phase AST matching),
  `m5-sandbox.svg` (bounded subprocess verdict contract),
  `m6-preference.svg` (Beta-Thompson sampling with 5% floor),
  `m7-rubric.svg` (Cohen's kappa with swap debiasing).
