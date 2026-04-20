# Architecture — {{PluginName}}

**Every file in this directory is auto-generated. Do not hand-edit.**

The generator at [generate.py](generate.py) reads the source-of-truth (`plugins/*/.claude-plugin/plugin.json`, `plugins/*/hooks/hooks.json`, `plugins/*/skills/*/SKILL.md`, `plugins/*/agents/*.md`) and writes:

| File | Purpose |
|------|---------|
| `highlevel.mmd` | Top-level system diagram — plugin → sub-plugin → engine |
| `hooks.mmd` | Hook lifecycle — events per sub-plugin, matcher → script |
| `lifecycle.mmd` | Session flow — when each sub-plugin activates |
| `dataflow.mmd` | Data flow across sub-plugins via enchanted-mcp events |
| `index.html` | Dark-themed single-page explorer combining all four |

## When to regenerate

Every time you change any of:

- A plugin's `.claude-plugin/plugin.json`
- A plugin's `hooks/hooks.json`
- A skill's `SKILL.md` frontmatter
- An agent's `agents/*.md` frontmatter

Run:

```bash
python docs/architecture/generate.py
```

Commit the regenerated `*.mmd` and `index.html` in the same commit that changed the source. Diagram drift is a code-review red flag.

## Why generation, not hand-edit

The 4-sibling consensus (allay + flux + hornet + reaper) treats this as a brand invariant: *diagrams never go stale*. Every sibling's `generate.py` reads the same source shapes and produces the same diagram types. If you edit a `.mmd` by hand, the next `generate.py` run overwrites you — and if no one ever runs `generate.py` again, the `.mmd` is wrong forever.

## Optional: SVG renders

If [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) is available (`cd docs/assets && npm install`), `generate.py` will produce SVG renders alongside the `.mmd` files. Pair this with `docs/assets/apply-blueprint.js` for the blueprint-style dark theme.

## Troubleshooting

- **`.mmd` file is one line of placeholder**: you haven't run `generate.py` yet. Template ships `.mmd` files as scaffolds; populate them by running the generator after your first sub-plugin is configured.
- **`generate.py` raises KeyError**: a plugin's `plugin.json` is missing a required field. Compare against any sibling plugin (`enchanted-plugins/flux/plugins/prompt-crafter/.claude-plugin/plugin.json`).
- **`index.html` renders blank**: the four `.mmd` files are stale or empty. Regenerate.
