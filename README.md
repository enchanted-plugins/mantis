# Mantis

<p>
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-3fb950?style=for-the-badge"></a>
  <img alt="6 plugins" src="https://img.shields.io/badge/Plugins-6-bc8cff?style=for-the-badge">
  <img alt="5 engines" src="https://img.shields.io/badge/Engines-M1%E2%80%93M7-58a6ff?style=for-the-badge">
  <img alt="3 agents" src="https://img.shields.io/badge/Agents-3-d29922?style=for-the-badge">
  <img alt="Phase 3 #6" src="https://img.shields.io/badge/Phase-3%20%236-f0883e?style=for-the-badge">
</p>

> **An @enchanted-plugins product — algorithm-driven, agent-managed, self-learning.**

Code review for AI-assisted development that catches runtime failures compile-time checks miss.

**6 sub-plugins. 5 engines. 3 slash commands. Bayesian per-developer preference. One command.**

> A PR adds `result = user_inputs[i] / n` with `n` coming from a JSON body. M1 Cousot Interval Propagation flags `n` as `[?, ?]` — unknown lower bound, possible zero. M2 Falleri Structural Diff confirms the assignment is new, not refactored. M5 Bounded Subprocess Dry-Run synthesizes a fuzzer input, executes the change in a `resource.setrlimit` sandbox, and observes `ZeroDivisionError`. M7 Zheng Pairwise Rubric judges: 5/10 Robustness, 2/10 Failure Resilience. M6 remembers that *this* developer consistently cares about divide-by-zero — next time the prior floor is 0.72, not 0.50. Verdict: HOLD with specific finding. Zero false positives from style noise. Weaver posts the finding on the PR.
>
> Time: under 5 seconds. Developer effort: read one finding, merge.

---

## Origin

Mantis takes its name from the **Mantis Lords of Hollow Knight** — gate-reviewers who judge worthiness through trial before letting you pass. Every PR is a supplicant at the gate; every engine is a test the code must survive. Joins Hornet (change comprehension) and Weaver (git flow) in the Hollow Knight cluster — three HK entities for three related dev-surface plugins is intentional brand signal.

The question this plugin answers: *Is this code good?*

## Contents

- [How It Works](#how-it-works)
- [What Makes Mantis Different](#what-makes-mantis-different)
- [The Full Lifecycle](#the-full-lifecycle)
- [Install](#install)
- [6 Sub-Plugins, 3 Agents, 5 Engines](#6-sub-plugins-3-agents-5-engines)
- [What You Get Per Review](#what-you-get-per-review)
- [The Science Behind Mantis](#the-science-behind-mantis)
- [vs Everything Else](#vs-everything-else)
- [Agent Conduct (10 Modules)](#agent-conduct-10-modules)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

## How It Works

Mantis runs a five-engine pipeline that treats code review as *static suspicion → sandboxed confirmation → Bayesian preference weighting → rubric judgment*. The premise: AI-assisted development ships two dominant bug classes that traditional review tools miss.

1. **Runtime failures that pass compile time.** `x / n` type-checks in every language; `n = 0` crashes at runtime. Static type systems don't catch it; neither does `cargo check` / `tsc`. Humans catch it on review, LLMs miss it.
2. **Reviewer fatigue on noisy signals.** GitHub Copilot, Cursor, and Qodo ship thousands of style suggestions, all at equal weight. Developers accept/reject without the tool learning. Over time, the signal-to-noise collapses and the reviewer disables the tool.

Mantis addresses both: the M1 static flagger feeds the M5 sandboxed confirmer (catches the first); M6 Bayesian preference accumulation per `(developer, rule)` (addresses the second). No existing reviewer ships either at zero-external-dep weight; both together is genuinely novel.

```
                  ┌─────────────────────────────────┐
                  │           Mantis                │
                  │    Phase 3 · Plugin #6          │
                  │   "Is this code good?"          │
                  └──────────────┬──────────────────┘
                                 │
    ┌────────┬────────┬──────────┼──────────┬────────┬────────┐
    │        │        │          │          │        │        │
┌───▼────┐ ┌─▼──────┐ ┌▼─────────▼┐ ┌───────▼┐ ┌─────▼──┐ ┌───▼────┐
│mantis- │ │mantis- │ │  mantis-  │ │mantis- │ │mantis- │ │mantis- │
│ core   │ │sandbox │ │preference │ │rubric  │ │python  │ │type-   │
│(M1+M2) │ │ (M5)   │ │  (M6)     │ │ (M7)   │ │(adapt) │ │script  │
└────────┘ └────────┘ └───────────┘ └────────┘ └────────┘ └────────┘
                                 │
                         ┌───────▼────────┐
                         │ mantis-verdict │
                         │ (DEPLOY/HOLD/  │
                         │  FAIL router)  │
                         └────────────────┘
```

## What Makes Mantis Different

### It catches runtime-only bugs via sandboxed confirmation

M1 Cousot Interval Propagation propagates abstract ranges (interval + nullability + container-shape lattices) across every assignment. A `/ n` operation flags when the interval includes zero. Then M5 Bounded Subprocess Dry-Run actually executes the change in a stdlib-only sandbox (`resource.setrlimit` + `signal.alarm` + subprocess isolation) and observes whether the bug reproduces. No other reviewer ships the static-suspicion → sandboxed-confirmation pipeline at zero-external-dep weight.

### Per-developer Bayesian preference accumulation

Every accept/reject on a rule updates a Beta-Binomial posterior per `(developer, rule)`. After 20 rejections of "use pathlib instead of os.path" from a developer who works on legacy Python 2 code, the posterior for that surface rule drops from 0.50 → 0.08. The 5% minimum floor keeps the rule alive for edge cases. Thompson sampling preserves exploration. The result: the tool *learns which signals this specific developer cares about* — instead of rubber-stamp-then-disable collapse.

### Inter-judge reliability via Cohen's Kappa

M7 Zheng Pairwise Rubric runs the judge twice with position-swapped inputs and reports Kappa — a measure of how consistent the LLM judge is with itself. If Kappa drops below 0.6, the verdict is flagged unstable and falls back to a rules-only decision. No other LLM-based reviewer reports inter-judge reliability.

### Cross-plugin signal routing

Mantis defers security-lane findings to Reaper (CWE classification, pattern databases) and change-classification to Hornet (Bayesian trust scoring per file). The three cooperate: Mantis catches code quality, Reaper catches security, Hornet catches unexpected-change risk. One PR, three orthogonal verdicts, no duplicate work.

## The Full Lifecycle

A review flows left to right through five stages. **M1 Cousot Interval Propagation** (mantis-core) propagates abstract ranges over the changed hunks, flagging suspicious assignments and divisions. **M2 Falleri Structural Diff** (mantis-core) clusters the changes by AST edit distance, so a 200-line rename collapses to one finding. **M5 Bounded Subprocess Dry-Run** (mantis-sandbox) sandbox-executes each flagged hunk and observes runtime behavior. **M6 Bayesian Preference Accumulation** (mantis-preference) weights findings by this developer's per-rule posterior. **M7 Zheng Pairwise Rubric Judgment** (mantis-rubric) scores the aggregate along a 5-axis rubric and routes the verdict through `mantis-verdict` (DEPLOY / HOLD / FAIL).

```
Session Start
     │
     ▼
┌──────────┐  ┌──────────┐  ┌──────────┐
│  Reaper  │─▶│  Hornet  │─▶│  Mantis  │
│ security │  │ changes  │  │  quality │
└──────────┘  └──────────┘  └────┬─────┘
                                 │
                            ┌────▼──────┐
                            │  Weaver   │
                            │ git flow  │
                            └───────────┘
                                 │
                            ┌────▼──────┐
                            │   Nook    │
                            │  cost     │
                            └───────────┘

Five Questions Answered:
  "What did I say?"     → Flux    (prompts)
  "What did I spend?"   → Allay   (tokens)
  "What just happened?" → Hornet  (changes)
  "Is it safe?"         → Reaper  (security)
  "What did it cost?"   → Nook    (spend)
  "Is it good?"         → Mantis  (quality)     ← you are here
```

Every stage is autonomous; the developer surface is pull (`/mantis-review`), not push.

## Install

Mantis ships as a 6-sub-plugin marketplace. One meta-plugin — `full` — lists all six as dependencies, so a single install pulls in the whole pipeline.

**In Claude Code** (recommended):

```
/plugin marketplace add enchanted-plugins/mantis
/plugin install full@mantis
```

Claude Code resolves the dependency list and installs all 6 sub-plugins. Verify with `/plugin list`.

**Want to cherry-pick?** Individual sub-plugins are still installable — e.g. `/plugin install mantis-core@mantis` if you only want the M1+M2 static surface. Sandbox-less / preference-less modes degrade gracefully; Mantis falls back to rules-only verdicts when an engine is missing.

## 6 Sub-Plugins, 3 Agents, 5 Engines

| Sub-plugin | Owns | Trigger | Agent |
|------------|------|---------|-------|
| [mantis-core](plugins/mantis-core/) | M1 Cousot Interval + M2 Falleri Structural Diff | skill-invoked | static-surface (Sonnet) |
| [mantis-sandbox](plugins/mantis-sandbox/) | M5 Bounded Subprocess Dry-Run | skill-invoked | sandbox-runner (Sonnet) |
| [mantis-preference](plugins/mantis-preference/) | M6 Bayesian Preference Accumulation | hook-driven (PostToolUse) | preference-learner (Haiku) |
| [mantis-rubric](plugins/mantis-rubric/) | M7 Zheng Pairwise Rubric Judgment | skill-invoked | rubric-judge (Sonnet) |
| [mantis-python](plugins/mantis-python/) | Python AST adapter | skill-invoked | — |
| [mantis-typescript](plugins/mantis-typescript/) | TypeScript AST adapter | skill-invoked | — |

Slash commands:

| Command | Function | Agent tier |
|---------|----------|------------|
| `/mantis-review <scope>` | On-demand deep review aggregating M1-M7 | Sonnet |
| `/mantis-explain <finding_id>` | Walk through why M1/M5/M7 flagged a specific finding | Sonnet |
| `/mantis-disable <rule_id>` | Permanent rule suppression with quarterly auto-reprompt | Haiku |

## What You Get Per Review

```
plugins/mantis-core/state/
├── findings.jsonl           M1+M2 flagged hunks with interval + diff cluster metadata
└── metrics.jsonl            per-scan timing + hunk counts

plugins/mantis-sandbox/state/
├── executions.jsonl         M5 sandbox runs with exit code, rlimit hit, observed exceptions
└── metrics.jsonl            sandbox run counts + avg latency

plugins/mantis-preference/state/
├── posteriors.json          per-(developer, rule) Beta-Binomial α/β parameters
├── learnings.json           cross-session preference accumulation (α=0.05)
└── metrics.jsonl            accept/reject events

plugins/mantis-rubric/state/
├── verdicts.jsonl           M7 5-axis scores + Kappa reliability per review
└── metrics.jsonl            rubric invocation metrics
```

Every review produces a JSONL row in `mantis-rubric/state/verdicts.jsonl` with the 5-axis rubric scores (Robustness, Specificity, Clarity, Failure Resilience, Determinism), the Cohen's Kappa reliability number, and the final verdict (DEPLOY / HOLD / FAIL).

## The Science Behind Mantis

Every Mantis engine is built on a formal mathematical model. Full derivations in [`docs/science/README.md`](docs/science/README.md).

$$\text{M1: } \text{Int}_v = [\text{lo}, \text{hi}] \sqcup \text{Null}(v) \sqcup \text{Shape}(v), \quad \text{widen after } N=3 \text{ iterations}$$

$$\text{M6: } P(\text{surface rule } r \mid \text{dev } d) = \max\left(0.05,\ \theta \sim \text{Beta}(\alpha_{d,r},\ \beta_{d,r})\right)$$

| ID | Name | Plugin | Algorithm |
|----|------|--------|-----------|
| M1 | Cousot Interval Propagation | mantis-core | Abstract interpretation over interval + nullability + container-shape lattices with threshold widening |
| M2 | Falleri Structural Diff | mantis-core | GumTree two-phase AST matching (top-down hash + bottom-up Dice) |
| M5 | Bounded Subprocess Dry-Run | mantis-sandbox | Stdlib `resource.setrlimit` + `signal.alarm` + subprocess sandbox (Unix-only) |
| M6 | Bayesian Preference Accumulation | mantis-preference | Beta-Binomial Thompson sampling per (developer, rule) with 5% minimum floor |
| M7 | Zheng Pairwise Rubric Judgment | mantis-rubric | 5-axis rubric + position-swap debiasing + Cohen's Kappa reliability |

**Defining engine:** M5 Bounded Subprocess Dry-Run — the static-suspicion → sandboxed-confirmation pipeline is the novel moat no existing reviewer ships at zero-external-dep weight.

Phase 2 adds M3 Yamaguchi Property-Graph Traversal, M4 Type-Reflected Invariant Synthesis, Schleimer Winnowing Clone Detection, O'Hearn Separation-Logic Bi-Abduction, and Cohort Similarity Borrowing.

## vs Everything Else

Honest comparison against adjacent tools. Marks `✓` only where the feature is present and production-ready.

| Feature | Mantis | GitHub Copilot | Cursor | Qodo Merge |
|---------|--------|----------------|--------|------------|
| Catches runtime-only bugs via sandboxed confirmation | ✓ | — | — | — |
| Per-developer Bayesian preference posterior | ✓ | — | — | — |
| Inter-judge reliability (Cohen's Kappa) reported | ✓ | — | — | — |
| Zero external runtime deps | ✓ | — | — | — |
| Markdown-file rule customization | ✓ | ✓ | ✓ | ✓ |
| Auto-generated PR comments | via Weaver | ✓ | ✓ | ✓ |
| Cross-plugin signal routing (Reaper, Hornet, Nook) | ✓ | — | — | — |

## Agent Conduct (10 Modules)

Every skill inherits a reusable behavioral contract from [shared/conduct/](shared/conduct/) — loaded once into [CLAUDE.md](CLAUDE.md), applied across all plugins. This is how Claude *acts* inside Mantis: deterministic, surgical, verifiable. Not a suggestion; a contract.

| Module | What it governs |
|--------|-----------------|
| [discipline.md](shared/conduct/discipline.md) | Coding conduct: think-first, simplicity, surgical edits, goal-driven loops |
| [context.md](shared/conduct/context.md) | Attention-budget hygiene, U-curve placement, checkpoint protocol |
| [verification.md](shared/conduct/verification.md) | Independent checks, baseline snapshots, dry-run for destructive ops |
| [delegation.md](shared/conduct/delegation.md) | Subagent contracts, tool whitelisting, parallel vs. serial rules |
| [failure-modes.md](shared/conduct/failure-modes.md) | 14-code taxonomy for accumulated-learning logs |
| [tool-use.md](shared/conduct/tool-use.md) | Tool-choice hygiene, error payload contract, parallel-dispatch rules |
| [formatting.md](shared/conduct/formatting.md) | Per-target format (XML / Markdown sandwich / minimal / few-shot), prefill + stop sequences |
| [skill-authoring.md](shared/conduct/skill-authoring.md) | SKILL.md frontmatter discipline, discovery test |
| [hooks.md](shared/conduct/hooks.md) | Advisory-only hooks, injection over denial, fail-open |
| [precedent.md](shared/conduct/precedent.md) | Log self-observed failures to `state/precedent-log.md`; consult before risky steps |

## Architecture

Interactive architecture explorer with sub-plugin diagrams, agent cards, and data flow:

**[docs/architecture/](docs/architecture/)** — auto-generated from the codebase. Run `python docs/architecture/generate.py` to regenerate.

Architecture diagrams are auto-generated from source-of-truth (`plugin.json`, `hooks.json`, `SKILL.md` frontmatter). Never hand-edited. The full synthesized architecture is at [docs/architecture/mantis-architecture.md](docs/architecture/mantis-architecture.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).

---

Repo: https://github.com/enchanted-plugins/mantis
