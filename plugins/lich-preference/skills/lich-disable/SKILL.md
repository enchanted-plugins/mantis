---
name: lich-disable
description: >
  Permanently suppresses a Lich rule for the current developer. Writes
  to plugins/lich-preference/state/overrides.json with a quarterly
  re-prompt date — the override auto-expires in 90 days unless the
  developer renews. Use when: the user runs /lich-disable <rule_id>.
  Do not use for: ambient preference learning (M6 Bayesian accumulation
  handles that automatically via accept/reject signals — don't conflate
  passive priors with explicit disables).
model: haiku
tools: [Read, Write]
---

# lich-disable

## Preconditions

- `plugins/lich-preference/state/overrides.json` exists (or will be created).
- Developer's identity is resolvable (git config user.email → SHA256 truncated to 12 chars).

## Inputs

- **Slash command**: `/lich-disable <rule_id>` (e.g. `/lich-disable lich-python:unused-import`).
- **Optional arg**: `--forever` skips the 90-day re-prompt (rare; discouraged).

## Steps

1. **Resolve developer_id.** Run `git config user.email`, SHA256 it, truncate to 12 hex chars. If not in a git repo, fall back to `os.environ["USER"]`.
2. **Validate rule_id.** Load `plugins/lich-preference/state/learnings.json`; confirm the rule_id exists in the per-developer posterior map. If not, check if it's a known rule from any Lich sub-plugin — if yes, add it to the overrides. If no, return an error; do not silently accept an unknown rule_id.
3. **Write override record.** Append to `plugins/lich-preference/state/overrides.json`:
   ```json
   {"developer_id": "...", "rule_id": "...", "disabled_at": "2026-04-20T...", "expires_at": "2026-07-19T...", "forever": false}
   ```
4. **Emit `lich.rule.disabled` event** (Phase 2 — file-write in Phase 1) with `{developer_id, rule_id, expiry_ts}`.
5. **Confirm to developer.** Print: "Rule `<rule_id>` disabled for <developer_id> until <expires_at>. Re-run `/lich-disable` to renew."

## Outputs

- `plugins/lich-preference/state/overrides.json` updated.
- stderr confirmation line.

## Handoff

The next M6 surfacing pass will read `overrides.json` and skip the disabled rule entirely (bypassing the 5% floor). A quarterly sweep (separate skill/hook in Phase 2) re-prompts the developer: "Still want `<rule_id>` disabled? [y/N]".

## Failure modes

- **F02 fabrication** — accepting an unknown rule_id; emit error instead.
- **F04 task drift** — if the developer asks `/lich-disable` to *tune* the posterior rather than disable — redirect: Bayesian accumulation (M6) handles tuning automatically; `/lich-disable` is only for hard suppression.
- **F14 version drift** — if `overrides.json` schema changes; Haiku validator auto-migrates with a one-pass rewrite.

## Why Haiku tier

This skill is a small state-mutation with a rigid contract (read → validate → append → confirm). Sonnet is overkill; Opus would burn budget. The validation step is shape-check, which Haiku handles well.
