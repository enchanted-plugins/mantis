# Lich — Agent Contract

Audience: Claude. Lich answers the developer's sixth question — *"Is this code good?"* — via a five-engine review pipeline: static suspicion (M1 Cousot Interval Propagation), structural change comprehension (M2 Falleri Structural Diff), sandboxed confirmation (M5 Bounded Subprocess Dry-Run), Bayesian preference accumulation (M6), and LLM rubric judgment (M7 Zheng Pairwise Rubric). Catches runtime failures that pass compile time; learns per-developer preferences with Bayesian uncertainty; defers security-lane findings to Hydra and change-classification to Crow.

## Shared behavioral modules

These apply to every skill in every plugin. Load once; do not re-derive.

- @../vis/packages/core/conduct/discipline.md — coding conduct: think-first, simplicity, surgical edits, goal-driven loops
- @../vis/packages/core/conduct/capability-fidelity.md — contracts survive capability gaps: recover, escalate, or abort; never silently substitute
- @../vis/packages/core/conduct/doubt-engine.md — adversarial self-check before agreement; fires on every "yes" and self-claim
- @../vis/packages/core/conduct/context.md — attention-budget hygiene, U-curve placement, checkpoint protocol
- @../vis/packages/core/conduct/verification.md — independent checks, baseline snapshots, dry-run for destructive ops
- @../vis/packages/core/conduct/verdict-calibration.md — every verdict (DEPLOY/PASS/COMPLETE/VERIFIED) carries n, sampling method, and a calibration qualifier; vis-side abstraction over the wixie DEPLOY bar
- @../vis/packages/core/conduct/delegation.md — subagent contracts, tool whitelisting, parallel vs. serial rules
- @../vis/packages/core/conduct/failure-modes.md — 14-code taxonomy for accumulated-learning logs
- @../vis/packages/core/conduct/tool-use.md — tool-choice hygiene, error payload contract, parallel-dispatch rules
- @../vis/packages/skills/conduct/formatting.md — per-target format (XML/Markdown/minimal/few-shot), prefill + stop sequences
- @../vis/packages/skills/conduct/skill-authoring.md — SKILL.md frontmatter discipline, discovery test
- @../vis/packages/core/conduct/hooks.md — advisory-only hooks, injection over denial, fail-open
- @../vis/packages/core/conduct/metacognition.md — periodic goal-restate; fires every K=8 tool-uses or on user meta-question
- @../vis/packages/core/conduct/precedent.md — log self-observed failures to `state/precedent-log.md`; consult before risky steps
- @../vis/packages/core/conduct/precedent-freshness.md — verify self-authored memory/precedent/briefings before relying on them: Class-A surfaces (path/function/flag) get a Glob/Grep existence check; Class-B snapshots get a git-log freshness check; Class-C feedback rules are trusted unless contradicted
- @../vis/packages/core/conduct/prior-art-discovery.md — F28 counter: run the 5-target discovery pass (shared/scripts, packages/*/skills, state/proposals, slug-glob, signature-grep) before authoring a new tool/script/skill/module
- @../vis/packages/core/conduct/reversibility-foresight.md — classify action reversibility (trivial/costly/impossible) before acting; confirmation scales with tier
- @../vis/packages/core/conduct/substrate-consumption.md — read-side complement to precedent.md: consume briefing, MEMORY, learnings, and precedent before acting; counter to F24 substrate-blindness
- @../vis/packages/core/conduct/sunk-cost-iteration.md — stop-and-re-ask after 2 INCONCLUSIVE/BLOCKED results on the same artifact; iteration is not an authorization to keep patching
- @../vis/packages/core/conduct/tier-sizing.md — prompt verbosity scales inversely with model tier; Haiku needs mechanical steps, Opus runs on intent
- @../vis/packages/web/conduct/web-fetch.md — external URL handling: cache, dedup, budget; WebFetch is Haiku-tier-only
- @../vis/packages/web/conduct/research-pipeline.md — multi-phase web research discipline: 6-phase shape, work-budget floors, mandatory adversarial round, 15-min wall-clock floor
- @../vis/packages/web/conduct/source-discipline.md — handling web-fetched evidence: untrusted-source quote wrapping, independence checks, τ, dissemination_score, source-type weighting
- @../vis/packages/web/conduct/citation-verification.md — trace + re-fetch protocol; Wayback Machine fallback; 4-class support taxonomy (Supported / Partial / Unsupported / Uncertain); refetch_pass_rate thresholds

When a module conflicts with a plugin-local instruction, the plugin wins — but log the override.

## Lifecycle

Lich is hybrid-triggered. PostToolUse hooks auto-review changes on Write/Edit/MultiEdit; skill-invocation is the manual handle for ad-hoc deep reviews.

| Event or Skill | Sub-plugin | Role |
|---|---|---|
| PostToolUse (Write\|Edit\|MultiEdit) | `lich-core` | Run M1 Cousot Interval Propagation + M2 Falleri Structural Diff on touched files |
| PostToolUse (Write\|Edit\|MultiEdit) | `lich-sandbox` | Run M5 Bounded Subprocess Dry-Run on M1-flagged call sites (Unix-only) |
| PostToolUse (Write\|Edit\|MultiEdit) | `lich-preference` | Update M6 Beta posteriors on developer accept/reject signals |
| PostToolUse (Write\|Edit\|MultiEdit) | `lich-rubric` | Run M7 Zheng Pairwise Rubric Judgment with position-swap debiasing |
| PostToolUse (end of PR) | `lich-verdict` | Compose M1-M7 outputs into DEPLOY/HOLD/FAIL verdict, emit `lich.review.completed` |
| `/lich-review <scope>` | `lich-core` | On-demand deep review, aggregating all sub-plugins |
| `/lich-explain <finding_id>` | `lich-rubric` | Walk through why M1/M5/M7 flagged something |
| `/lich-disable <rule_id>` | `lich-preference` | Permanent suppression with auto-reprompt quarterly |

See `./plugins/<name>/hooks/hooks.json` for matchers. Agents in `./plugins/<name>/agents/`.

## Algorithms

M1 Cousot Interval Propagation · M2 Falleri Structural Diff · M5 Bounded Subprocess Dry-Run · M6 Bayesian Preference Accumulation · M7 Zheng Pairwise Rubric Judgment. Derivations in `docs/architecture/lich-architecture.md`. **Defining engine:** M5 Bounded Subprocess Dry-Run — the static-suspicion → sandboxed-confirmation pipeline is the novel moat no existing reviewer ships at zero-external-dep weight.

| ID | Name | Plugin | Algorithm |
|----|------|--------|-----------|
| M1 | Cousot Interval Propagation | lich-core | Abstract interpretation over interval + nullability + container-shape lattices with threshold widening (N=3) |
| M2 | Falleri Structural Diff | lich-core | GumTree two-phase AST matching (top-down hash + bottom-up Dice containment) |
| M5 | Bounded Subprocess Dry-Run | lich-sandbox | Stdlib `resource.setrlimit` (CPU/AS/NOFILE/FSIZE) + `signal.alarm` + subprocess sandbox on Unix |
| M6 | Bayesian Preference Accumulation | lich-preference | Beta-Binomial Thompson sampling per (developer, rule) with 5% minimum surfacing floor |
| M7 | Zheng Pairwise Rubric Judgment | lich-rubric | 5-axis rubric + position-swap debiasing + Cohen's Kappa inter-judge reliability |

Phase 2 adds M3 Yamaguchi Property-Graph Traversal (Joern CPG), M4 Type-Reflected Invariant Synthesis (Hypothesis-ghostwriter upgrade to M5 inputs), Schleimer Winnowing Clone Detection, O'Hearn Separation-Logic Bi-Abduction (Java/C++/ObjC), and Cohort Similarity Borrowing (M6 cold-start).

## Behavioral contracts

Markers: **[H]** hook-enforced (deterministic) · **[A]** advisory (relies on your adherence).

1. **[H] IMPORTANT — Lich never re-scans CWE-tagged security findings.** Hydra's R3 OWASP Vulnerability Graph owns CWE taxonomy (98 CWEs across 2,011 patterns). If Hydra's `plugins/vuln-detector/state/audit.jsonl` has a finding on the file, Lich *boosts* M6 review-attention weight for that file and annotates M7's rubric input with "Security context: Hydra flagged {cwe} {severity}" — but never re-classifies or re-reports the CWE. The non-duplication contract with Hydra is load-bearing; breaking it fractures the severity source of truth across two plugins.

2. **[H] YOU MUST NOT relax M5 sandbox resource caps.** The caps are `RLIMIT_CPU=5s`, `RLIMIT_AS=512MB`, `RLIMIT_NOFILE=16`, `RLIMIT_FSIZE=10MB`, `signal.alarm=10s`. These are load-bearing — relaxing any cap converts the sandbox into an arbitrary-code-execution risk on every PR. Changes require a documented security review. Windows platforms skip M5 entirely and emit `platform-unsupported` in the verdict; never silently pretend M5 ran.

3. **[A] YOU MUST report Cohen's Kappa alongside M7 scores.** The honest-numbers contract is the product. When M7's two judge passes disagree beyond the per-axis threshold (Kappa < 0.4), the axis is flagged "unstable" in the PDF report — never collapsed into a hidden average. A single bare M7 score without its Kappa is a contract violation.

4. **[A] YOU MUST respect M6's 5% sampling floor.** A rule's Beta posterior can deprioritize but must still surface at ≥ 5% Thompson probability. One rejection does not kill a rule. Permanent suppression is the developer's explicit action via `/lich-disable <rule>` — which writes to `plugins/lich-preference/state/overrides.json` with a dated quarterly re-prompt. Do not drop a rule to 0% through accumulated rejections alone.

5. **[A] ESCALATE on confirmed runtime failure.** When M5 confirms a bug with a concrete witness input (e.g., `n=0` for a div-zero site), the verdict is FAIL, not HOLD. Confirmed bugs are facts, not probabilities — they do not get averaged with rubric scores.

6. **[A] Consume Crow's V1 + V2 output, never re-classify.** Crow's `plugins/change-tracker/state/audit.jsonl` is the authoritative change classifier. Lich reviews what Crow flagged; Lich does not peer-classify. If Crow scored a change `trust=0.62`, that weight propagates into M6's attention prior unchanged.

## Verdict bar

| Verdict | M1 condition | M5 condition | M6 condition | M7 condition | Action |
|---------|-------------|--------------|--------------|--------------|--------|
| DEPLOY | All flagged sites severity < HIGH | No confirmed runtime failure | ≥ 80% surfaced findings posterior mean > 0.5 | All 5 axes ≥ 3.5/5 AND Kappa ≥ 0.4 | Silent pass; write to `state/verdict.jsonl` |
| HOLD | 1-2 HIGH flags, no CRITICAL | Any timeout-without-confirmation | ≥ 50% surfaced posterior > 0.3 | Any axis < 3.5 OR Kappa < 0.4 | Surface to reviewer; Sylph warns |
| FAIL | Any CRITICAL OR ≥ 3 HIGH flags | Any confirmed runtime failure | (posterior cannot downgrade from HOLD to FAIL) | Any axis ≤ 2 OR > 2 axes < 3 | Block; Sylph refuses auto-commit |

## State paths

| State file | Owner | Purpose |
|---|---|---|
| `plugins/lich-core/state/learnings.json` | lich-core | Per-session learnings for Gauss Accumulation (M1/M2 parameter tuning) |
| `plugins/lich-core/state/precedent-log.md` | lich-core | Self-observed operational failures (see @../vis/packages/core/conduct/precedent.md) |
| `plugins/lich-sandbox/state/run-log.jsonl` | lich-sandbox | M5 sandbox run history (CPU used, timeouts, confirmed bugs) |
| `plugins/lich-preference/state/learnings.json` | lich-preference | Per-(developer, rule) Beta(α, β) posteriors |
| `plugins/lich-preference/state/overrides.json` | lich-preference | Developer-disabled rules with quarterly re-prompt dates |
| `plugins/lich-rubric/config/rubric-v1.json` | lich-rubric | Versioned M7 rubric definition (ship-time config) |
| `plugins/lich-rubric/state/kappa-log.jsonl` | lich-rubric | Per-axis Cohen's Kappa history (runtime, gitignored) |
| `plugins/lich-verdict/state/verdict.jsonl` | lich-verdict | Append-only DEPLOY/HOLD/FAIL record per review |
| `shared/learnings.json` | exporter | Cross-plugin aggregated learnings |

## Agent tiers

| Tier | Model | Used for |
|---|---|---|
| Orchestrator | Opus | M7 disagreement adjudication; cross-engine verdict synthesis when engines conflict |
| Executor | Sonnet | M7 default judge; lich-core analyzer loops; M2 structural diff when LOC > 1k |
| Validator | Haiku | M7 budget fallback (when Pech fires `pech.budget.threshold.crossed`); rubric-schema freshness; M6 posterior integrity audit |

Respect the tiering. Routing a Haiku validation task to Opus burns budget and breaks the cost contract with Pech.

## Anti-patterns

- **Duplicating Hydra's R3.** Re-scanning for CWE-89 / CWE-79 / CWE-918 or any other CWE-tagged security finding in M1. Counter: M1 is scoped to correctness (div-zero, null deref, OOB, overflow, resource balance). Security taint sinks stay Hydra's lane.
- **Silent M5 skip on Windows.** Emitting a green verdict when M5 didn't run because `resource` is absent. Counter: honest note `platform-unsupported` in the verdict; M1-only runtime-failure judgment with reduced confidence.
- **Bare M7 score without Kappa.** Averaging two judge runs silently when they disagree. Counter: report Kappa per axis; flag axes with Kappa < 0.4 as "unstable" in PDF.
- **Rule death from accumulated rejections.** Dropping a rule's surface probability to 0 without the developer's explicit `/lich-disable`. Counter: Thompson sampling with 5% floor.
- **Unbounded M5 sandbox.** Running developer code without `resource.setrlimit` caps. Counter: the five caps (CPU/AS/NOFILE/FSIZE + signal.alarm) are load-bearing; relaxation requires documented security review.
- **Peer-classifying changes that Crow already classified.** Re-running semantic diff in Lich when Crow's V1 is authoritative. Counter: consume Crow's output, propagate its trust score into M6 priors.
- **M7 self-preference in the judge model family.** Using the same model family to judge its own output. Counter: when the target of review was generated by Claude (usually the case in Claude Code sessions), surface this in the PDF as a known bias vector, not hidden.

---

## Brand invariants (survive unchanged into every sibling)

1. **Zero external runtime deps.** Hooks: bash + jq only. Scripts: Python 3.8+ stdlib only. No npm/pip/cargo at runtime (dev-only renderer deps in `docs/assets/package.json` are the one exception, and are never imported from plugin code).
2. **Managed agent tiers.** Opus = orchestrator/judgment. Sonnet = executor/loops. Haiku = validator/format.
3. **Named formal algorithm per engine.** ID prefix letter + number. Academic-style name: `[Method] [Domain] [Action]`.
4. **Emu-style marketplace.** Each sub-plugin ships `.claude-plugin/plugin.json` + `{agents,commands,hooks,skills,state}/` + `README.md`.
5. **Dark-themed PDF report.** Produced by `docs/architecture/generate.py` + `puppeteer.config.json` on final release.
6. **Gauss Accumulation learning.** Per-session learnings at `plugins/<name>/state/learnings.json`; exported to `shared/learnings.json`.
7. **enchanted-mcp event bus.** Inter-plugin coordination via published/subscribed events namespaced `lich.<event>`.
8. **Diagrams from source of truth.** `docs/architecture/generate.py` reads `plugin.json` + `hooks.json` + `SKILL.md` frontmatter → writes four mermaid diagrams + `index.html`. Diagrams are never hand-edited.

Events this plugin publishes: `lich.review.completed`, `lich.rule.disabled`, `lich.sandbox.failed`
Events this plugin subscribes to: `crow.change.classified`, `hydra.vuln.detected`, `pech.budget.threshold.crossed`, `emu.runway.threshold.crossed`
