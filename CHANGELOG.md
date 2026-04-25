# Changelog

All notable changes to `lich` are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] — rename: lich identity, standardized origin format

Lich is Phase 3 #6 in the @enchanted-plugins ecosystem rollout and has not shipped a public release yet. This file captures the scaffolding and docs landing ahead of v0.1.0.

### Added
- Tier-1 governance docs: `SECURITY.md`, `SUPPORT.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`.
- `.github/` scaffold: issue templates, PR template, CODEOWNERS, dependabot config.
- Tier-2 docs: `docs/getting-started.md`, `docs/installation.md`, `docs/troubleshooting.md`, `docs/adr/README.md`.

### Planned for [0.1.0]
- 6 sub-plugins covering code review for AI-assisted development: lich-core, lich-preference, lich-python, lich-rubric, lich-sandbox, lich-typescript, lich-verdict (+ `full` meta-plugin).
- 5 named engines — M1 Cousot Interval Propagation, M2 Falleri Structural Diff, M5 Bounded Subprocess Dry-Run, M6 Bayesian Per-Developer Preference, M7 Zheng Pairwise Rubric — full derivations in [docs/science/README.md](docs/science/README.md).
- 3 agents across tiers; orchestrator on Opus, executors on Sonnet, validators on Haiku.
- `/lich-review` slash command: static suspicion → sandboxed confirmation → Bayesian weighting → rubric judgment.
- Integration with Sylph's PR lifecycle: findings posted on the PR body when `/sylph:pr` is used.

Track progress in [ROADMAP.md](docs/ROADMAP.md) and the [ecosystem map](docs/ecosystem.md).

[Unreleased]: https://github.com/enchanted-plugins/lich/commits/main
