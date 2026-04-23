# Plugin.json Audit — 2026-04-21

Auditor: config-audit pass per the 7-field contract in root `CLAUDE.md` § Brand invariant 4 (Emu-style marketplace). One line per plugin. JSON round-trip verified on all 8 files.

## Contract

Fields required: `name`, `version` (SemVer), `description` (≤120 char), `author.name`, `license`, `repository.type=git` + `url`, `keywords` (≥3).

## Baseline: repo LICENSE

Repo root `LICENSE` is **MIT** (Copyright 2026 enchanted-plugins). All plugin.json `license` fields match.

## Per-plugin state

| Plugin | name | version | description | author | license | repository | keywords | Delta |
|---|---|---|---|---|---|---|---|---|
| lich-core | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-sandbox | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-preference | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-rubric | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-verdict | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-python | ok | ok | ok | ok | ok | **added** | ok (4) | +repository |
| lich-typescript | ok | ok | ok | ok | ok | **added** | ok (5) | +repository |
| full | ok | ok | ok | ok | ok | **added** | ok (3) | +repository |

## Findings

- **Single gap, 8 plugins:** `repository` missing across every plugin.json. Patched with `{type:"git", url:"https://github.com/enchanted-plugins/enchanted-skills.git"}`.
- No version bumps (per scope fence — maintainer owns SemVer).
- No license divergence. No placeholder descriptions. No sub-3 keyword arrays.
- JSON round-trip via `json.load`: 8/8 parse clean post-edit.
