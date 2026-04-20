# docs/assets — rendered diagrams & equations

Pre-rendered SVGs so GitHub's mobile app (which renders neither
` ```mermaid ` blocks nor `$$...$$` math) shows them correctly. The
root `README.md` references the files here as `<img>`.

## Template note

Schematic is the **canonical template** for every sibling plugin in
the enchanted-plugins ecosystem. This folder ships as a scaffold:
the rendering toolchain configs (`mermaid.config.json`,
`puppeteer.config.json`, `apply-blueprint.js`, `render-math.js`,
`package.json`) are committed; no SVGs are rendered here because
the template itself is never installed or displayed.

When you clone this template to start a new sibling:

1. Populate the sub-plugins under `plugins/`.
2. Run `python docs/architecture/generate.py` to generate the `.mmd`
   sources under `docs/architecture/`.
3. Run the render commands below to produce the `.svg` files here.
4. Commit the `.mmd` and `.svg` together.

## Files (after first generate)

| File | Source | Regenerate |
|------|--------|-----------|
| `highlevel.svg` | `../architecture/highlevel.mmd` | `npx @mermaid-js/mermaid-cli -i ../architecture/highlevel.mmd -o highlevel.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js highlevel.svg` |
| `hooks.svg` | `../architecture/hooks.mmd` | `npx @mermaid-js/mermaid-cli -i ../architecture/hooks.mmd -o hooks.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js hooks.svg` |
| `lifecycle.svg` | `../architecture/lifecycle.mmd` | `npx @mermaid-js/mermaid-cli -i ../architecture/lifecycle.mmd -o lifecycle.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js lifecycle.svg` |
| `dataflow.svg` | `../architecture/dataflow.mmd` | `npx @mermaid-js/mermaid-cli -i ../architecture/dataflow.mmd -o dataflow.svg -c mermaid.config.json -p puppeteer.config.json -b "#0a1628" -w 1800 && node apply-blueprint.js dataflow.svg` |
| `math/*.svg` | `render-math.js` | `npm install --prefix . mathjax-full && node render-math.js` |

Run the commands from `docs/assets/` (paths are relative). The
toolchain (`node_modules/`, `package-lock.json`) is gitignored; only
the rendered SVGs, their `.mmd` sources, and the configs committed
above are tracked.

The `apply-blueprint.js` step overlays an engineering-blueprint grid
(navy `#0a1628` paper, `#1e3a5f` major lines / `#16304f` minor lines)
onto the rendered diagram so it reads as a CAD drawing rather than a
neutral dark card. This is the shared visual identity across every
sibling repo (allay, flux, hornet, nook, reaper, weaver).
