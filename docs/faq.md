# Frequently asked questions

Quick answers to questions that don't yet have their own doc. For anything deeper, follow the links — the full answer usually lives in a neighboring file.

## What's the difference between Lich and the other siblings?

Lich answers *"is this code good?"* — it runs a five-engine pipeline over a diff and emits a verdict per finding. Sibling plugins answer different questions in the same session: Wixie engineers prompts, Emu tracks token spend, Crow watches change trust, Hydra scans for security surface, Sylph coordinates git workflow. All are independent installs. See [docs/ecosystem.md](ecosystem.md) for the full map.

## Do I need the other siblings to use Lich?

No. Lich is self-contained — once v0.1.0 ships, installing `full@lich` gets every command working standalone. If Sylph is present, Lich findings are surfaced on the PR body when `/sylph:pr` opens a PR, but this is opportunistic integration, not a dependency.

## How do I report a bug vs. ask a question vs. disclose a security issue?

- **Security vulnerability** — private advisory, never a public issue. See [SECURITY.md](../SECURITY.md).
- **Reproducible bug** — a bug report issue with repro steps + exact versions.
- **Usage question or half-formed idea** — [Discussions](https://github.com/enchanted-plugins/lich/discussions).

The [SUPPORT.md](../SUPPORT.md) page has the exact links for each.

## Is Lich an official Anthropic product?

No. Lich is an independent open-source plugin for [Claude Code](https://github.com/anthropics/claude-code) (Anthropic's CLI). It's published by [enchanted-plugins](https://github.com/enchanted-plugins) under the MIT license and is not affiliated with, endorsed by, or supported by Anthropic.

## Is Lich available now?

No. Lich is Phase 3 #6 in the @enchanted-plugins rollout and is pre-release. The README and the engine IDs (M1, M2, M5, M6, M7) describe the committed public surface, but no v0.1.0 tag has shipped yet. Track progress in [docs/ROADMAP.md](ROADMAP.md) and the [ecosystem map](https://github.com/enchanted-plugins/wixie/blob/main/docs/ecosystem.md).

## What languages does Lich support?

Two at launch: Python (via `lich-python`) and TypeScript (via `lich-typescript`). M1 Cousot Interval Propagation + M5 Bounded Subprocess Dry-Run have language-specific adapters; M2 Falleri Structural Diff, M6 Bayesian preference, and M7 Zheng rubric are language-agnostic. Additional language adapters would land as new sub-plugins.
