# Lich — Product Architecture

*Phase 3 · Plugin #6 · enchanted-plugins · answers the developer's sixth question: "Is this code good?"*

**Name.** Lich. After the Lich Lords of Hollow Knight — gate-reviewers who judge worthiness through trial before letting you pass. Every PR is a supplicant at the gate; every engine is a test the code must survive. Lich joins Crow (change comprehension) and Sylph (git flow) in the Hollow Knight cluster — three HK entities for three related dev-surface plugins is intentional brand signal. This slot previously carried the placeholder name "Athena"; that name is retired because Athena is a pre-existing mythological figure Supergiant borrowed, not a game-native entity. Lich Lords are game-native and pass the naming convention.

**Engine prefix.** M (single letter, unique across F/A/V/S/W/L).

**Trigger model.** Hybrid. PostToolUse hook subscribes to Crow's change-classification signal (Phase 1: file-tail of `crow/plugins/change-tracker/state/audit.jsonl`; Phase 2: `crow.change.classified` event via enchanted-mcp) and auto-reviews affected hunks. Skill-invoked commands (`/lich-review`, `/lich-explain`) are the manual handle for ad-hoc deep reviews. Silent on DEPLOY, surfacing on HOLD/FAIL.

**Panel composition.** This document synthesizes four expert lenses: static-analysis researcher, dynamic-analysis & fuzzing engineer, code-review & developer-preference expert, and enchanted-plugins architect. Disagreements are surfaced before resolution.

---

## Layer 1: Language Substrate & Parser Strategy

**Prior art.** Python's stdlib `ast` module is the canonical zero-dep AST source; it parses Python 3.8+ source into a typed tree, supports `ast.walk`, `ast.NodeVisitor`, and `ast.unparse` since 3.9. For TypeScript, three realistic options: (1) `tsc --noEmit --generateTrace <dir>` subprocess-call and parse the JSON trace output, (2) ship a tree-sitter Wasm binary (tree-sitter/tree-sitter-typescript, ~3MB) and invoke via a tiny Python loader, (3) use `esprima-python` (pure-Python, but only JS not TS). Semgrep uses tree-sitter in production for 20+ languages; biome uses a hand-rolled Rust parser.

**Options comparison.**

| Option | Ship weight | Dep class | Precision on TS generics | Pitfall |
|--------|------------|-----------|--------------------------|---------|
| stdlib `ast` for Python | 0MB | stdlib | N/A | Python-only |
| `tsc --generateTrace` subprocess | 0MB shipped | runtime-optional (`tsc` must exist in repo) | Highest (compiler-grade) | Fails offline / on repos without TypeScript devDep installed |
| tree-sitter Wasm | ~3MB | bundled | Medium (no type-resolution) | Ship-weight burden; Wasm runtime (wasmtime-py) would add another 10MB+ — disqualifying |
| `esprima-python` stdlib-ish | 0MB | pure-Python wheel | Low (JS-only, no TS syntax) | Fails on `interface`, generics, decorators |

**Recommendation.** Python substrate uses stdlib `ast` (zero dispute). TypeScript substrate uses `tsc --noEmit --generateTrace` subprocess with a graceful fallback: if `tsc` is not resolvable in the repo's `node_modules/.bin/` or `PATH`, Lich's TypeScript adapter emits a one-line status `ts-parse-unavailable` and skips M1/M2 for `.ts`/`.tsx` files — Lich still runs M6/M7 on the diff text. Never silently pretend TS analysis ran. Tree-sitter Wasm deferred to Phase 2 as an optional "bundled-parser" plugin for offline users.

**Pitfall.** Static-analysis researcher warned that `tsc --generateTrace` is undocumented for AST extraction; its output format is a profiling trace, not a stable AST. Accepted risk: if the format changes across `tsc` versions, Lich's adapter pins a tested range (`>=5.0,<6.0`) and emits a compatibility warning outside it. This is the kind of precision/portability tradeoff the prior-art's three candidates all make somewhere.

---

## Layer 2: M1 Cousot Interval Propagation — Abstract Domains & Widening

**Prior art.** Cousot & Cousot POPL'77 formalized abstract interpretation as a Galois connection between concrete and abstract domains. Every sound analyzer (Astrée, Polyspace, Mopsa, Facebook Infer Pulse, Microsoft pyright's type-narrowing) descends from this framework. The core tradeoff the paper names is between *precision* (how tight the abstract approximation is) and *termination* (widening must fire at some lattice height to guarantee fixpoint convergence on loops). Intervals are the simplest non-trivial abstract domain: `⊥ ≤ [lo, hi] ≤ ⊤` with `⊥` empty and `⊤ = (-∞, +∞)`. Nullability adds `{Null, NotNull, MaybeNull, ⊤}`. Shape tracking for containers adds `{Empty, NonEmpty, Unknown}`. pyright ships exactly this trio plus type-narrowing for control-flow contexts.

**Options comparison.**

| Widening strategy | Terminates | Precision | Pitfall |
|-------------------|-----------|-----------|---------|
| No widening | Never on unbounded loops | Perfect | Hangs — disqualified |
| Jump to `⊤` after N iterations (N=3) | Always after 3 iters | Low (loses all range info on loops) | Loses div-zero evidence on loop-carried divisors |
| Threshold widening (N=3, then jump to nearest threshold in `{0, 1, 255, 65535, MAXINT}`) | Always after 3 iters | Medium | Thresholds must be language-aware (e.g. u8 vs. i32) |
| Narrowing after widening (1 pass) | Always | Medium-High | Extra pass cost; still loses precision on non-obvious invariants |

**Recommendation.** Threshold widening with N=3 and a language-aware threshold set: `{Null, 0, 1, -1, sys.maxsize, -sys.maxsize}` for Python integers + nullability; `{null, undefined, 0, 1, -1, Number.MAX_SAFE_INTEGER}` for TS. One narrowing pass after widening. Lattice height per variable is bounded at 8 (4 bits) — the analyzer stops refining a variable after 8 lattice visits and marks it `⊤`. Dynamic-analysis engineer pushed for no widening ("the whole point is precision"); static-analysis researcher overrode with termination being non-negotiable — Lich must never hang on a user's loop.

**Pitfall.** Abstract interpretation is sound on the *properties it models* and silent on everything else. M1 will miss bugs it was never told to look for — e.g., a `dict.get(k)` returning `None` won't be caught unless the nullability domain explicitly models `Optional[T]` unpacking. Document this limit in the sub-plugin README; don't sell soundness as correctness. Source: [Cousot & Cousot POPL'77](https://www.di.ens.fr/~cousot/COUSOTpapers/publications.www/CousotCousot-POPL-77-ACM-p238--252-1977.pdf).

---

## Layer 3: M2 Falleri Structural Diff — GumTree Parameter Defaults

**Prior art.** Falleri et al. ASE 2014 "Fine-grained and Accurate Source Code Differencing" introduced GumTree: a two-phase algorithm that (1) greedily matches isomorphic subtrees top-down by hash (phase 1), then (2) matches remaining nodes bottom-up by Dice coefficient on descendant-match ratios (phase 2). Key parameters: `min_height` (don't match single-leaf subtrees — too noisy), `min_dice` (similarity threshold for bottom-up containment match), `min_similarity` (accept partial matches only above this threshold). Paper defaults: `min_height=2`, `min_dice=0.5`, `min_similarity=0.5`. These are tuned for fine-grained differencing across arbitrary code; code-review cares more about *semantic* edits than perfect granularity.

**Options comparison.**

| Parameter set | Move/rename recovery | False-positive noise | Runtime on 500-LOC diff | Pitfall |
|---------------|---------------------|---------------------|------------------------|---------|
| Paper defaults (2, 0.5, 0.5) | High | Medium (many leaf-level matches) | 250ms | Reviewer sees too many "trivial rename" edits |
| Conservative (3, 0.6, 0.7) | Medium-high | Low | 180ms | Misses some cross-function moves |
| Aggressive (1, 0.3, 0.3) | Very high | High (false moves on similar subtrees) | 400ms | Match pollution — spurious move edits confuse reviewer |

**Recommendation.** Conservative defaults: `min_height=3`, `min_dice=0.6`, `min_similarity=0.7`. Code-review use prefers fewer, higher-confidence semantic edits over exhaustive fine-grained differencing. Reviewer's attention is the scarce resource; M2's job is to surface the 3-5 semantic edits that matter, not the 40-node leaf-match noise. When M2 is uncertain (partial match below threshold), it falls back to the hunk's unified diff and flags the fallback — honest-numbers contract.

**Pitfall.** GumTree's bottom-up phase is O(n·m) in worst case on dense subtree similarity. On a 10k-LOC file the analyzer can spike to several seconds. M2 enforces a 2-second per-file budget; if exceeded, it times out and emits `structural-diff-timeout`, falling back to unified diff. Never block the reviewer on a pathological case. Source: [Falleri et al. ASE'14 preprint (HAL)](https://hal.science/hal-01054552/document).

---

## Layer 4: M5 Bounded Subprocess Dry-Run — Sandbox Policy

**Prior art.** Python stdlib `resource` module exposes `setrlimit(RLIMIT_CPU, seconds)`, `setrlimit(RLIMIT_AS, bytes)` (address space), `setrlimit(RLIMIT_NOFILE, count)` (open file descriptors), `setrlimit(RLIMIT_FSIZE, bytes)` (max file size). Available on POSIX only — absent from Windows stdlib. `signal.alarm(seconds)` fires SIGALRM after wall-clock time; `subprocess.run(..., timeout=)` is the portable fallback. Docker/Firecracker/gVisor give stronger isolation but are explicitly out-of-scope (zero-dep invariant). Pyodide ships Python-in-Wasm — 10MB+ wheel, disqualified. CrossHair (pschanely/CrossHair) is the closest pure-Python precedent but requires z3-solver wheel. The minimum sandbox that catches the developer's canonical `1/0` example is: fork a subprocess, set rlimits in a `preexec_fn`, set `signal.alarm` to a shorter wall-clock cap, feed the synthesized input to a `runpy`-wrapped function, catch stderr + exit code, parse.

**Options comparison.**

| Isolation level | Ship weight | Windows support | ACE risk | Pitfall |
|-----------------|------------|-----------------|----------|---------|
| `resource.setrlimit` + `signal.alarm` + subprocess + env scrub | 0MB | No | Low (capped CPU/RSS/FD, no network by env) | Unix-only; Windows falls back to timeout-only, weaker |
| Docker rootless | 10s of MB on host + daemon | Yes via Docker Desktop | Very low | Ship-weight; assumes Docker installed |
| Firecracker microVM | 100+ MB | No (Linux KVM only) | Negligible | Disqualified — ship-weight + kernel requirement |
| No sandbox (just subprocess + timeout) | 0MB | Yes | Medium-High | Network calls, filesystem writes, fork bombs |

**Recommendation.** Stdlib `resource.setrlimit` + `signal.alarm` on Unix, exposed as the `lich-sandbox` sub-plugin. Exact caps at launch:

- `RLIMIT_CPU = 5` (5 CPU-seconds; infinite loop ≈ timeout at 5s of CPU)
- `RLIMIT_AS = 512 * 1024 * 1024` (512 MB address space cap)
- `RLIMIT_NOFILE = 16` (16 open FDs; enough for stdlib + 3-4 temp files)
- `RLIMIT_FSIZE = 10 * 1024 * 1024` (10 MB per-file write cap)
- `signal.alarm(10)` (10s wall-clock; kicks in ahead of CPU on pathological I/O)

Network isolation: scrub `HTTP_PROXY`, `HTTPS_PROXY`, and set `no_proxy=*`; refuse to ship sockets in the harness; document that developers running malicious code through Lich should also use OS-level network blocks (the plugin cannot fully guarantee network denial from Python alone). Filesystem isolation: write-target limited to a per-run `tempfile.mkdtemp()` that's deleted on exit; reads are unrestricted (analysis-only, no containment of *reads*). Windows: skip M5 entirely at launch; emit `platform-unsupported` in the verdict with an honest note. Phase 2 adds a Job Objects backend for Windows.

**Pitfall.** Sandbox without caps is arbitrary-code-execution on every PR — the plugin becomes an attack vector. The `resource.setrlimit` caps are load-bearing; any relaxation requires a documented security review. Static-analysis researcher and plugin-brand architect aligned strongly here; fuzzing engineer noted that sandbox-escape via untrusted C extensions loaded by Python is still possible (via `ctypes` with a forged shared library). Mitigation: sandbox runs under a non-privileged user whenever possible, and the Lich README flags this limit honestly.

---

## Layer 5: M5 Input Synthesis — Boundary Values at Launch, M4 Phase 2

**Prior art.** Hypothesis (HypothesisWorks/hypothesis) ghostwriter inspects function signatures + type annotations and synthesizes `@given` test stubs in seconds using `inspect.signature` + `typing.get_type_hints`. Property-based testing's "boundary values" tradition: for each input type, try the 5-10 values most likely to break generic functions (`0`, `-1`, `None`, `""`, `[]`, `{}`, `sys.maxsize`). For M1-flagged variables specifically, the flag itself names the suspect value (`n ∈ [-∞, +∞]` suspected at `x / n` → try `n = 0`).

**Options comparison.**

| Synthesis strategy | Coverage on 10 failure classes | Dep weight | Pitfall |
|--------------------|--------------------------------|-----------|---------|
| Boundary values from flag | Div-zero ✓, null ✓, OOB partial, overflow ✓ | 0MB | Misses structure-sensitive bugs (need valid parse, invalid content) |
| Hypothesis ghostwriter (external) | 9/10 classes | `hypothesis` wheel (~2MB) | Violates zero-dep if bundled; optional-install acceptable |
| CrossHair symbolic execution | 10/10 but slow | `z3-solver` wheel (~8MB) | Out-of-dep scope; invoke only if developer has it installed |
| Random + coverage-guided | 8/10 | 0MB | Slow convergence; no guarantee on bug-triggering inputs within budget |

**Recommendation.** MVP (Phase 1) uses boundary-value synthesis driven directly by M1 flags: for each flagged variable, Lich tries a per-type default set (`{0, -1, None, "", [], sys.maxsize}` for Python int/str/list), runs the containing function in M5's sandbox, catches `ZeroDivisionError`, `TypeError`, `IndexError`, `OverflowError`. If Hypothesis is installed in the developer's environment, Lich's adapter detects it and upgrades to ghostwriter-synthesized stubs (Phase 2 engine M4 — gated behind Hypothesis availability). CrossHair is the Phase 3 moonshot, behind the developer's explicit opt-in.

| Failure class | Boundary synthesis | Hypothesis (P2) | CrossHair (P3) |
|---------------|-------------------|-----------------|----------------|
| Divide-by-zero | ✓ (from M1 `n=0`) | ✓ | ✓ |
| Null/None deref | ✓ | ✓ | ✓ |
| Array OOB | partial | ✓ | ✓ |
| Integer overflow | ✓ (`sys.maxsize + 1`) | ✓ | ✓ |
| Unhandled exception | ✓ | ✓ | ✓ |
| Infinite loop | ✓ (via `signal.alarm`) | ✓ | partial |

**Pitfall.** Boundary-value synthesis is weakest on structure-sensitive inputs — a function that parses JSON won't fail on `"0"` but will fail on `'{"malformed": }'`. M1+M5 explicitly does not claim to find those; that's Phase 2 M4 territory. Document the coverage honestly.

---

## Layer 6: M6 Bayesian Preference Accumulation — Priors, Updates, Floor

**Prior art.** Thompson 1933 introduced Thompson sampling for the two-armed bandit. Russo & Van Roy (Stanford 2018) "A Tutorial on Thompson Sampling" is the modern reference. For binary preference (accept/reject a rule's finding), Beta-Binomial is the conjugate prior: `Beta(α, β)` parameterizes the posterior after `α-1` accepts and `β-1` rejects (starting from Beta(1,1) uniform). Sampling surfaces a rule with probability proportional to a draw from its Beta posterior — rules with high mean and low variance surface often; rules with high variance still get exploration. No shipped reviewer does this today: Copilot uses markdown `copilot-instructions.md`; Cursor uses "memories" (auto-generated notes, not a posterior); Qodo Merge accumulates team-level rules, not per-developer. JetBrains Mellum uses ML-ranked completion ordering, not rule-level preference. Lich M6 is genuinely novel territory.

**Options comparison.**

| Algorithm | Cold-start | Over-fit resistance | Uncertainty-native | Pitfall |
|-----------|-----------|--------------------|--------------------|---------|
| Beta-Binomial Thompson | Uniform Beta(1,1) | Strong (two rejections moves posterior by ~0.1 mean) | Yes (sample from posterior) | Cold-start means high variance for new developer; paired with Phase 2 Cohort Similarity |
| Logistic regression on features | Zero-weight coefficients | Weak (drifts on few samples) | No native | Needs feature engineering; not Bayesian |
| Elo | Each rule at 1500 | Medium | No | Competitive-pairs vocabulary; doesn't encode individual accept-rate cleanly |
| Contextual bandit (LinUCB) | Uniform | Strong with regularization | Yes (confidence bound) | Needs context vector per rule; over-engineered for MVP |

**Recommendation.** Beta-Binomial Thompson sampling. Initial prior `Beta(α=1, β=1)` uniform for all (developer, rule) pairs. Update rule: accept → α++; reject → β++; overrode → treat as 0.5 accept + 0.5 reject (ambiguous signal, update both). Surface probability on each review pass is a Thompson sample from the posterior. Minimum sampling floor: 5% (if the Thompson sample would surface below 5%, upgrade it to 5%). This prevents a rule from being "dead" after a few rejections; the developer can permanently suppress via `/lich-disable <rule>` which writes to an overrides file. Overrides auto-expire with a quarterly "still disabled?" prompt — anti-drift protocol.

Persistence: `plugins/lich-preference/state/learnings.json` — one file per developer ID, containing a map `{rule_id → {alpha, beta, surface_count, accept_count, reject_count, last_update}}`. Atomic writes via `os.rename` (brand standard A4 Atomic State Serialization pattern).

**Pitfall.** Preference expert warned of the cold-start problem: for a new developer, all posteriors are `Beta(1,1)`, so all rules surface at 50% probability on PR #1. Developer sees ~2× more surfaces than a tuned system, potentially causing churn-rejection before the posterior informs. Mitigation: ship Phase 2's Cohort Similarity Borrowing (inherit priors from similar developers on the same repo). MVP accepts the 2-3-week cold-start noise as an honest tradeoff. Source: [Thompson sampling tutorial](https://web.stanford.edu/~bvr/pubs/TS_Tutorial.pdf).

---

## Layer 7: M7 Zheng Pairwise Rubric Judgment — Rubric & Debiasing

**Prior art.** Zheng et al. 2023 "MT-Bench" demonstrated LLM-as-judge with pairwise comparisons, identifying position bias (the first-presented answer scores higher on average) and self-preference bias (a judge prefers outputs from its own model family). Mitigations: swap order and average, use a different model family for judge vs. judged, and report inter-judge reliability via Cohen's Kappa. Recursive Rubric Decomposition (2025) showed that breaking a monolithic quality metric into 5-8 independent sub-axes and judging each axis separately reduces variance vs. a single holistic score. Code-review-specific rubric work (CodeScore 2024, GPTScore extensions) narrows the axes to: Clarity, Correctness, Idiom-fit, Testability, Simplicity, Maintainability, Performance-awareness.

**Options comparison.**

| Rubric design | Variance | Reviewer alignment | Cost (tokens/review) | Pitfall |
|---------------|----------|-------------------|---------------------|---------|
| 1 holistic axis | High | Low | ~500 | High judge-to-judge disagreement |
| 3 axes (Clarity / Correctness / Idiom) | Medium | Medium | ~1500 | Misses testability and simplicity signals |
| 5 axes (above + Testability + Simplicity) | Low | High | ~2500 | Slightly expensive; may over-surface rubric noise |
| 8 axes | Low | High but with overlap (Simplicity ⊆ Clarity?) | ~4000 | Axis correlation; axes should be orthogonal |

**Recommendation.** 5 orthogonal axes at launch:

1. **Clarity** — Can a reviewer summarize this function's purpose in one sentence after 30 seconds?
2. **Correctness-at-glance** — Are the guards (null checks, boundary checks, error handling) visible without tracing?
3. **Idiom-fit** — Does the code match this language's and this repo's conventions?
4. **Testability** — Are there seams for unit tests without mocking the universe?
5. **Simplicity** — Is the solution the simplest that solves the stated problem?

Scoring scale 1-5 per axis (not 1-10; 1-5 is easier to justify across judges). Debiasing protocol: for every diff, judge twice with (before, after) then (after, before); if per-axis delta ≥ 1.5, escalate to Opus adjudicator. Inter-judge reliability: Cohen's Kappa computed per axis across the two runs; Kappa < 0.4 flags the axis as "unstable" in the PDF report — honest-numbers signal, never hidden behind an average.

Model tier: Sonnet default judge. Haiku when Pech's `pech.budget.threshold.crossed` fires at 80% (cost-aware downshift, brand-standard cost contract). Opus reserved for disagreement adjudication only — cost-contract respected.

**Pitfall.** Position bias is real even with a single swap — a 2-sample average is still noisy. Lich's Kappa reporting is the honest-numbers floor: if Kappa is unstable, the axis output is explicitly flagged rather than averaged silently. Preference expert pushed for 8 axes; static-analysis researcher pushed for 3. Compromise: 5 axes at launch, Phase 2 survey data from real review flows informs whether to expand or consolidate. Source: [MT-Bench (Zheng et al. 2023)](https://arxiv.org/abs/2306.05685).

---

## Layer 8: Verdict Contract — DEPLOY / HOLD / FAIL Thresholds

**Prior art.** Wixie's CLAUDE.md defines the three-verdict vocabulary: DEPLOY (σ < 0.45 AND overall ≥ 9.0 AND all 5 axes ≥ 7.0 AND 8/8 SAT assertions pass), HOLD (any axis < 7.0 or σ ≥ 0.45), FAIL (reviewer flags a structural issue). Lich adopts the same three-verdict shape but defines its own threshold math because the axes and assertions differ.

**Options comparison.**

| Verdict math | Interpretability | False-positive rate (est) | Pitfall |
|--------------|-----------------|---------------------------|---------|
| Any HIGH/CRITICAL M1 finding → FAIL | High | Low | Brittle on false-positive prone rules |
| Composite score blending M1/M5/M7 with weights | Medium | Medium | Weights are vibes unless tuned |
| Hard floor per engine with explicit AND | High | Medium | No composite signal across engines |
| Ensemble: HARD floors per engine AND composite tie-breaker | High | Low | More logic to document |

**Recommendation.** Ensemble with hard floors plus a composite tie-breaker:

| Verdict | M1 condition | M5 condition | M6 condition | M7 condition | Action |
|---------|-------------|--------------|--------------|--------------|--------|
| **DEPLOY** | All flagged sites severity < HIGH | No confirmed runtime failure | ≥ 80% surfaced findings have posterior mean > 0.5 | All 5 axes ≥ 3.5/5 AND Kappa ≥ 0.4 | Silent pass; write DEPLOY to `state/verdict.jsonl` |
| **HOLD** | 1-2 HIGH flags AND no CRITICAL | Any timeout-without-confirmation | ≥ 50% posterior > 0.3 | Any axis < 3.5 OR Kappa < 0.4 | Surface to reviewer with per-engine breakdown; Sylph's pre-commit gate warns |
| **FAIL** | Any CRITICAL flag OR ≥ 3 HIGH flags | Any confirmed runtime failure | (N/A — posterior doesn't downgrade from HOLD to FAIL) | Any axis ≤ 2/5 OR > 2 axes < 3 | Block; Sylph's pre-commit gate refuses auto-commit; developer acknowledgment required |

**Pitfall.** A single M5-confirmed runtime failure (e.g., div-by-zero with a concrete witness) is load-bearing evidence — it's not a probability, it's a fact. The verdict math treats confirmed failures as hard FAIL triggers, not soft scores. Inversely, M7 rubric scores are genuinely subjective; they inform HOLD but can never single-handedly fail a PR. This asymmetry is intentional and documented. Source: [Wixie CLAUDE.md § DEPLOY bar](../../wixie/CLAUDE.md).

---

## Layer 9: Hydra, Crow, Emu, Pech, Sylph Integration Contract

**Prior art.** The enchanted-plugins ecosystem uses per-plugin `audit.jsonl` files as the Phase 1 source of truth (enchanted-mcp event bus is Phase 2). Hydra's `plugins/vuln-detector/state/audit.jsonl` records are shape `{event:"vuln_detected", ts, file, line, vuln_id, cwe, severity, description, language, tool}` (verified via direct file inspection). Crow's `plugins/change-tracker/state/audit.jsonl` records classify changes. Emu publishes token/cost metrics to `plugins/*/state/metrics.jsonl`. Pech (building) will publish budget-threshold events. Sylph's pre-commit gate already subscribes to `pech.budget.threshold.crossed` and will subscribe to `lich.review.completed` per brand-standard event-envelope convention.

**Options comparison.**

| Consumption mechanism | Coupling | Phase 1 ready | Pitfall |
|-----------------------|---------|---------------|---------|
| Tail-read sibling `audit.jsonl` | Tight (file path + record shape) | Yes | Breaks if sibling renames files or changes schema |
| MCP event subscription | Loose (envelope named, sibling implements) | No (Phase 2) | Forward-looking only |
| Shared SQLite | Loose | Would need new plugin | Over-engineered for MVP |
| stdin/stdout pipe | Tight, runtime-coupled | No (Claude Code doesn't pipe plugins) | Not a supported mechanism |

**Recommendation.** Phase 1 uses file-tailing of sibling `audit.jsonl` files with a pinned record-shape contract in Lich's `hooks/record-shapes.json`. Phase 2 migrates to MCP event subscription with the same payload shape.

**Record shapes Lich consumes (Phase 1):**

```json
// hydra/plugins/vuln-detector/state/audit.jsonl → used as M6 attention weight
{"event":"vuln_detected","ts":"2026-04-19T12:34:56Z","file":"src/api.ts","line":42,
 "vuln_id":"sql-injection-template-literal","cwe":"CWE-89","severity":"critical",
 "description":"...","language":"typescript","tool":"Write"}

// crow/plugins/change-tracker/state/audit.jsonl → used to detect what Lich should review
{"event":"change.classified","ts":"2026-04-19T12:34:57Z","file":"src/api.ts",
 "classification":"behavior-change","trust_score":0.62,"hunks":[...]}
```

**Record shape Lich emits (`plugins/verdict/state/verdict.jsonl`):**

```json
{"event":"review.completed","ts":"2026-04-19T12:35:02Z","pr_id":"local-20260419-1234",
 "verdict":"HOLD","engines":{"M1":{...},"M2":{...},"M5":{...},"M6":{...},"M7":{...}},
 "rubric_scores":{"clarity":4,"correctness":3,"idiom":4,"testability":3,"simplicity":4},
 "kappa":{"clarity":0.72,"correctness":0.68,...}}
```

**Non-duplication invariants.**
- Lich NEVER re-scans for CWE-89 SQL injection, CWE-79 XSS, CWE-918 SSRF, or any other CWE-tagged security finding. These are Hydra R3's lane. If Hydra's audit.jsonl already has a CRITICAL CWE on the file, Lich *increases review-attention weight* for M6 and adds a "Security context: Hydra flagged {cwe} {severity}" note to M7's rubric input, but does not re-classify the finding.
- Lich NEVER re-classifies a change. Crow's V1 Semantic Diff + V2 Bayesian Trust output is authoritative. Lich is a *consumer*, not a peer classifier.
- Lich consumes but does not mutate Emu's token metrics. M7 judge-tier downshift to Haiku under Pech budget pressure is the only cross-plugin control flow Lich exercises.

### Event-Bus Contract

**Publishes:**

| Event | Trigger | Payload | Consumer examples |
|-------|---------|---------|-------------------|
| `lich.review.completed` | End of PostToolUse Lich review | `{pr_id, verdict, engines{M1..M7}, rubric_scores, kappa}` | Sylph pre-commit gate, Pech cost attribution, audit dashboard |
| `lich.rule.disabled` | Developer runs `/lich-disable <rule>` | `{developer_id, rule_id, ts, expiry_ts}` | Preference-accumulator archival; quarterly re-prompt scheduler |
| `lich.sandbox.failed` | M5 sandbox errored (not a bug — infra failure) | `{file, function, error_class, retry_budget}` | Observability; never inflates a verdict |

**Subscribes:**

| Event | Source | Effect |
|-------|--------|--------|
| `crow.change.classified` | Crow | Triggers Lich review on hunks above a configurable trust threshold |
| `hydra.vuln.detected` | Hydra | Boosts M6 attention weight for the affected file; adds security context note to M7 input |
| `pech.budget.threshold.crossed` | Pech | Downshifts M7 judge from Sonnet → Haiku at 80% budget |
| `emu.runway.threshold.crossed` | Emu | Pauses M5 sandbox runs (CPU budget conservation) until runway recovers |

---

## Layer 10: Sub-Plugin Breakdown & Developer Query Path

**Prior art.** Emu-style marketplace: one sub-plugin per engine OR one per orthogonal concern (language adapter, cross-plugin router, developer-UX surface). Hydra ships 5 sub-plugins (secret-scanner, vuln-detector, action-guard, config-shield, audit-trail); Sylph ships 5 similarly organized. The meta `full` plugin pulls all siblings via dependency resolution.

**Options comparison.**

| Sub-plugin count | Cognitive load | Meta-plugin complexity | Pitfall |
|------------------|---------------|-----------------------|---------|
| 3 (core + sandbox + verdict) | Low | Simple | Buries language adapters inside core; user can't disable one |
| 5 (core + sandbox + preference + rubric + verdict) | Medium | Simple | Language adapters still bundled |
| 7 (above + lich-python + lich-typescript) | Medium-high | Simple | Adapters are the right surface but add install ceremony |
| 10+ | High | Complex | Over-sliced; developers confused |

**Recommendation.** 7 sub-plugins at launch + `full` meta:

| Sub-plugin | Owns | Category |
|------------|------|----------|
| `lich-core` | M1 Cousot Interval Propagation + M2 Falleri Structural Diff + verdict synthesizer | analyzer |
| `lich-sandbox` | M5 Bounded Subprocess Dry-Run (Unix-only at launch) | analyzer |
| `lich-preference` | M6 Bayesian Preference Accumulation | learning |
| `lich-rubric` | M7 Zheng Pairwise Rubric Judgment | judgment |
| `lich-python` | Python language adapter (ruff rule-ID mapping, Python-specific idioms) | adapter |
| `lich-typescript` | TypeScript language adapter (biome rule-ID mapping, TS-specific idioms) | adapter |
| `lich-verdict` | Cross-sub-plugin verdict composition + event emission | router |
| `full` | Meta-plugin pulling all 7 via dependency resolution | meta |

Developer surfaces Day 1:

- **Skill**: `/lich-review <hunk|file|PR>` — on-demand deep review
- **Skill**: `/lich-explain <finding_id>` — walks through why M1/M5/M7 flagged something
- **Skill**: `/lich-disable <rule_id>` — permanent suppression with auto-reprompt quarterly
- **Hook** (PostToolUse, Write|Edit): passive review on every change; silent on DEPLOY, loud on HOLD/FAIL
- **Status-line badge**: "M: 2 HOLDs, 1 FAIL" — ambient awareness
- **PDF report**: dark-themed, per-session, showing M1 findings heatmap + M7 radar chart + M6 posterior histogram — post-session audit

Deferred: web dashboard (Phase 4 with enchanted-mcp), IDE hover-tips (VSCode extension, Phase 3), Slack bot (Phase 4).

**Pitfall.** Every sub-plugin is a maintenance surface. 7 is the upper bound of what a two-week MVP can credibly scaffold *and* keep healthy. If any sub-plugin ships without tests or a clear owner-module, it drags the whole plugin's reliability. Plugin-brand architect overrode preference expert's push for 9 sub-plugins (adding separate rubric-judge and rubric-aggregator); collapsed into lich-rubric to stay within scope.

---

### Recommended Full Stack Summary

| Layer | Choice | Why | Pitfall avoided |
|-------|--------|-----|-----------------|
| Language substrate — Python | stdlib `ast` | Zero-dep; canonical | N/A |
| Language substrate — TypeScript | `tsc --generateTrace` subprocess, graceful fallback | Compiler-grade precision when available, honest skip when not | Hidden silent failure |
| M1 abstract domains | Intervals + nullability + container-shape | Catches 4 of the 10 runtime-failure classes statically | Over-engineering heap analysis for MVP |
| M1 widening | Threshold widening, N=3, language-aware thresholds | Guarantees termination without collapsing to ⊤ | Infinite loop on user's loop |
| M2 GumTree defaults | Conservative (3, 0.6, 0.7) + 2s budget | Reviewer attention is the scarce resource | Noise from leaf-level matches |
| M5 sandbox | `resource.setrlimit` + `signal.alarm` + env scrub | Stdlib-only, catches 80% of runtime failures | ACE risk from uncapped execution |
| M5 platform | Unix-only at launch; Windows skips with honest note | `resource` module absent on Windows | Silent pretense that M5 ran |
| M5 input synthesis | Boundary values from M1 flags; Hypothesis upgrade if installed | Zero-dep MVP with graceful upgrade | Over-commit to Hypothesis as hard dep |
| M6 algorithm | Beta-Binomial Thompson sampling with 5% floor | Bayesian, uncertainty-aware, matches Gauss Accumulation brand | Rule death from single rejection |
| M7 rubric | 5 orthogonal axes, position-swap + Cohen's Kappa | Honest-numbers on subjective scoring | Hidden judge disagreement |
| M7 judge tiers | Sonnet default, Haiku under Pech budget, Opus adjudication | Cost contract with Pech | Uncontrolled Opus spend |
| Verdict | DEPLOY/HOLD/FAIL with hard floors + composite | Interpretable + catches both kinds of bugs | Weighted-score vibes |
| Hydra integration | File-read of `vuln-detector/state/audit.jsonl` (P1), event (P2) | Non-duplication of R3 | Double-reporting CWEs |
| Crow integration | Subscribe to `crow.change.classified` | Consumer, not re-classifier | Peer-level drift |
| Sub-plugin count | 7 + `full` meta | Emu-style sliceable granularity | Over-slicing |

### Plugin Package Layout

```
lich/
├── .claude-plugin/
│   └── marketplace.json
├── CLAUDE.md
├── CONTRIBUTING.md
├── README.md
├── install.sh
├── LICENSE
├── shared/
│   ├── conduct/              (10 behavioral modules — unchanged from schematic)
│   ├── constants.sh
│   ├── metrics.sh
│   ├── sanitize.sh
│   └── scripts/
├── plugins/
│   ├── lich-core/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/lich-analyzer.md
│   │   ├── commands/lich-review.md
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-review/SKILL.md
│   │   ├── state/{learnings.json,precedent-log.md}
│   │   └── README.md
│   ├── lich-sandbox/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/lich-sandbox-runner.md
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-sandbox/SKILL.md
│   │   ├── state/
│   │   └── README.md
│   ├── lich-preference/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/lich-preference-updater.md
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-disable/SKILL.md
│   │   ├── state/{learnings.json,overrides.json}
│   │   └── README.md
│   ├── lich-rubric/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/lich-judge.md
│   │   ├── config/rubric-v1.json
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-explain/SKILL.md
│   │   ├── state/kappa-log.jsonl
│   │   └── README.md
│   ├── lich-python/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-python/SKILL.md
│   │   ├── config/ruff-rule-map.json
│   │   └── README.md
│   ├── lich-typescript/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── hooks/hooks.json
│   │   ├── skills/lich-typescript/SKILL.md
│   │   ├── config/biome-rule-map.json
│   │   └── README.md
│   ├── lich-verdict/
│   │   ├── .claude-plugin/plugin.json
│   │   ├── agents/lich-verdict-synthesizer.md
│   │   ├── hooks/hooks.json
│   │   ├── state/verdict.jsonl
│   │   └── README.md
│   └── full/
│       ├── .claude-plugin/plugin.json
│       └── README.md
├── docs/
│   ├── architecture/
│   │   ├── generate.py
│   │   ├── lich-architecture.md    ← this document
│   │   ├── highlevel.mmd
│   │   ├── dataflow.mmd
│   │   ├── lifecycle.mmd
│   │   ├── hooks.mmd
│   │   └── index.html
│   └── brand-guide.md
└── tests/
    └── run-all.sh
```

### Lich Named Engines

| ID | Name | Sub-plugin | Algorithm | Source |
|----|------|-----------|-----------|--------|
| M1 | Cousot Interval Propagation | lich-core | Abstract interpretation over interval + nullability + container-shape lattices with threshold widening | Cousot & Cousot POPL'77 |
| M2 | Falleri Structural Diff | lich-core | GumTree two-phase AST matching (top-down hash + bottom-up Dice) | Falleri et al. ASE'14 |
| M3 | Yamaguchi Property-Graph Traversal | (Phase 2) | Code Property Graph over unified AST+CFG+PDG with Gremlin-like queries | Yamaguchi et al. S&P'14 (Joern) |
| M4 | Type-Reflected Invariant Synthesis | (Phase 2) | Hypothesis-ghostwriter-style synthesis from `inspect.signature` + `typing.get_type_hints` | Hypothesis project |
| M5 | Bounded Subprocess Dry-Run | lich-sandbox | Stdlib `resource.setrlimit` + `signal.alarm` + subprocess sandbox | Python stdlib; novel composition for code review |
| M6 | Bayesian Preference Accumulation | lich-preference | Beta-Binomial Thompson sampling per (developer, rule) with 5% minimum sampling floor | Thompson 1933; Russo & Van Roy 2018 |
| M7 | Zheng Pairwise Rubric Judgment | lich-rubric | 5-axis rubric + position-swap debiasing + Cohen's Kappa inter-judge reliability | Zheng et al. MT-Bench 2023 |

### Event-Bus Contract

**Publishes:**

| Event | Trigger | Payload shape | Consumer examples |
|-------|---------|---------------|-------------------|
| `lich.review.completed` | End of Lich review pass | `{pr_id, verdict, engines, rubric_scores, kappa}` | Sylph pre-commit gate; Pech attribution |
| `lich.rule.disabled` | Developer `/lich-disable` | `{developer_id, rule_id, expiry_ts}` | Preference archival; re-prompt scheduler |
| `lich.sandbox.failed` | M5 infra failure (not a finding) | `{file, function, error_class}` | Observability |

**Subscribes:**

| Event | Source | Effect on Lich |
|-------|--------|------------------|
| `crow.change.classified` | Crow | Trigger review on affected hunks |
| `hydra.vuln.detected` | Hydra | Boost M6 attention weight; annotate M7 input |
| `pech.budget.threshold.crossed` | Pech | Downshift M7 judge (Sonnet → Haiku) at 80% |
| `emu.runway.threshold.crossed` | Emu | Pause M5 sandbox runs until runway recovers |

### Runtime-Failure Coverage Matrix

Columns: M1 (Cousot Interval Propagation), M5 (Bounded Subprocess Dry-Run), M6 (Bayesian prioritization — ranks which findings reviewer sees first, not a detector), M7 (Zheng Pairwise Rubric — style/clarity signals).

| Failure class | M1 | M5 | M6 | M7 | Notes |
|---------------|:--:|:--:|:--:|:--:|------|
| Divide-by-zero | ✓ | ✓ | ranks | — | Canonical case; flagged statically, confirmed dynamically |
| Null / None deref | ✓ | ✓ | ranks | — | Via nullability domain |
| Array out-of-bounds | partial | ✓ | ranks | — | M1 weak on unknown-length inputs; M5 catches |
| Integer overflow / underflow | ✓ | partial | ranks | — | M1 with `sys.maxsize` threshold; M5 confirms on Python (rarer than C) |
| Unhandled exception propagation | partial | ✓ | ranks | — | M5 catches via stderr inspection |
| Race conditions / deadlocks | — | — | ranks | partial | Out of scope for MVP; M7 may note concurrency concerns |
| Infinite loops / unbounded recursion | partial | ✓ | ranks | — | M5's `signal.alarm` catches |
| Memory leaks / use-after-free (native) | — | — | ranks | — | Requires Phase 2 M3 + Phase 3 CrossHair |
| Resource leaks (file handles, DB conns) | partial | ✓ | ranks | ✓ | M1 flags unclosed `open()`; M5 confirms via FD count; M7 rubric notes |
| Time-of-check-to-time-of-use (TOCTOU) | — | — | ranks | partial | Out of MVP; Hydra's R3 catches security-flavored TOCTOU; Lich covers dataflow variant in Phase 2 |

(Note: **CWE-tagged security sinks** — SQL injection, XSS, SSRF, etc. — are explicitly Hydra's R3 lane, not Lich's. The row is omitted here because it's not a Lich responsibility.)

### Language Adapter Contract

| Adapter | Top linter mapped | Rule count at launch | Universal rules (from common core) | Language-specific rules |
|---------|-------------------|---------------------|-----------------------------------|------------------------|
| `lich-python` | ruff (astral-sh) | ~120 mapped / ~900 available | 40 (dead code, complexity, naming, duplicates) | 80 Python-only (pyupgrade idioms, list/dict-comp, async-await patterns, typing modernization) |
| `lich-typescript` | biome (biomejs) | ~80 mapped / ~423 available | 35 (same universal core) | 45 TS-only (hooks exhaustive deps, JSX a11y, `any`-avoidance, narrow-type-guards) |
| *(Phase 2)* `lich-rust` | clippy | ~80 mapped / ~550 available | 35 (same core) | 45 Rust-only (borrow idioms, lifetime placement, `clone` avoidance) |
| *(Phase 2)* `lich-go` | golangci-lint | ~40 mapped / ~100 aggregator | 30 (same core) | 10 Go-only (err-check-pattern, context-propagation) |
| *(Phase 2)* `lich-java` | Checkstyle + PMD | ~80 mapped / ~600 total | 40 | 40 Java-only (Effective Java items) |

### Preference Posterior Schema

```json
{
  "schema_version": "1.0",
  "developer_id": "git-email-sha256-truncated-to-12",
  "repo_id": "path-sha256-truncated-to-12",
  "last_update": "2026-04-19T12:34:56Z",
  "rules": {
    "lich-python:unused-import": {
      "alpha": 3,
      "beta": 7,
      "surface_count": 10,
      "accept_count": 2,
      "reject_count": 6,
      "override_count": 2,
      "last_update": "2026-04-19T12:34:56Z"
    },
    "lich-core:M1-division-by-zero": {
      "alpha": 8,
      "beta": 2,
      "surface_count": 10,
      "accept_count": 7,
      "reject_count": 1,
      "override_count": 2,
      "last_update": "2026-04-19T12:34:56Z"
    }
  },
  "cohort_prior": {
    "source": "repo-cohort",
    "version": "phase-2",
    "_note": "Phase 2 — populated by Cohort Similarity Borrowing"
  }
}
```

### Rubric Schema

```json
{
  "rubric_version": "1.0",
  "axes": [
    {"id": "clarity", "scale": 5, "definition": "Can a reviewer summarize this function's purpose in one sentence after 30 seconds?"},
    {"id": "correctness_at_glance", "scale": 5, "definition": "Are guards (null checks, boundary checks, error handling) visible without tracing?"},
    {"id": "idiom_fit", "scale": 5, "definition": "Does the code match this language's and this repo's conventions?"},
    {"id": "testability", "scale": 5, "definition": "Are there seams for unit tests without mocking the universe?"},
    {"id": "simplicity", "scale": 5, "definition": "Is the solution the simplest that solves the stated problem?"}
  ],
  "scoring_scale": {"min": 1, "max": 5, "half_points": false},
  "position_swap": {"enabled": true, "runs": 2, "escalate_if_delta_gte": 1.5},
  "kappa": {"report_per_axis": true, "unstable_threshold": 0.4, "action_on_unstable": "flag_in_pdf"},
  "judge_model_tiers": {
    "default": "claude-sonnet-4-6",
    "under_budget_pressure": "claude-haiku-4-5-20251001",
    "adjudication": "claude-opus-4-7"
  },
  "prompt_template_ref": "plugins/lich-rubric/skills/lich-explain/rubric-prompt.md"
}
```

### Verdict Contract

| Verdict | M1 condition | M5 condition | M6 condition | M7 condition | Action |
|---------|-------------|--------------|--------------|--------------|--------|
| DEPLOY | All flagged sites severity < HIGH | No confirmed runtime failure | ≥ 80% surfaced findings posterior mean > 0.5 | All 5 axes ≥ 3.5/5 AND Kappa ≥ 0.4 | Silent pass; write to `state/verdict.jsonl` |
| HOLD | 1-2 HIGH flags, no CRITICAL | Any timeout-without-confirmation | ≥ 50% surfaced posterior > 0.3 | Any axis < 3.5 OR Kappa < 0.4 | Surface to reviewer; Sylph warns |
| FAIL | Any CRITICAL OR ≥ 3 HIGH flags | Any confirmed runtime failure | (N/A — posterior doesn't failure-downgrade) | Any axis ≤ 2 OR > 2 axes < 3 | Block; Sylph refuses auto-commit |

### MVP vs. Full Build

**Phase 1 — 2-week MVP.**
- Engines: M1 Cousot Interval Propagation + M2 Falleri Structural Diff + M5 Bounded Subprocess Dry-Run + M6 Bayesian Preference Accumulation + M7 Zheng Pairwise Rubric Judgment.
- Languages: `lich-python` + `lich-typescript` only.
- Platform: Unix-only for M5; Windows skips M5 with honest note.
- Integration: File-based reads from Hydra/Crow audit.jsonl; no MCP yet.
- Surfaces: `/lich-review`, `/lich-explain`, `/lich-disable`, PostToolUse hook, status-line badge, PDF report.
- Exit criteria: all 7 sub-plugins installable via `full` meta, pass smoke test `tests/run-all.sh`, architecture doc shipped.

**Phase 2 — 2-3 month full build.**
- Engines added: M3 Yamaguchi Property-Graph Traversal (Joern-style CPG substrate), M4 Type-Reflected Invariant Synthesis (Hypothesis-ghostwriter upgrade to M5 input synthesis), Schleimer Winnowing Clone Detection (code duplicate detection), O'Hearn Separation-Logic Bi-Abduction (Java/C++/ObjC resource-ownership), Cohort Similarity Borrowing (M6 cold-start via cohort priors).
- Languages added: `lich-rust` + `lich-go` + `lich-java` + `lich-kotlin`.
- Platform: M5 Windows support via Job Objects backend.
- Integration: Migrate file-reads to MCP event bus (`crow.change.classified`, `hydra.vuln.detected`, `pech.budget.threshold.crossed`).
- Additional surfaces: VSCode extension hover-tips; Slack bot on PR events.

### Draft CLAUDE.md

*Fills schematic's 8-section canonical shape. See [CLAUDE.md](../../CLAUDE.md) for the rendered file; this section summarizes the fills.*

- **Shared behavioral modules** — unchanged (10 `@shared/conduct/*.md` references, verbatim from schematic).
- **Lifecycle** — hybrid trigger. PostToolUse hook (Write|Edit|MultiEdit) drives `lich-core`, `lich-sandbox`, `lich-preference` passes. SessionStart hook (rare — only when `config/rubric-v1.json` needs refresh). Skill commands (`/lich-review`, `/lich-explain`, `/lich-disable`).
- **Algorithms** — M1–M7 named engines; M1+M2 in lich-core, M5 in lich-sandbox, M6 in lich-preference, M7 in lich-rubric. Defining engine: **M5 Bounded Subprocess Dry-Run** (the novel pipeline moat).
- **Behavioral contracts**:
  1. **[H] IMPORTANT — Lich never re-scans CWE-tagged security findings.** If Hydra's audit.jsonl has a finding on the file, Lich boosts attention weight but does not re-classify. This is the non-duplication contract with Hydra R3.
  2. **[H] YOU MUST NOT relax M5 sandbox caps.** `RLIMIT_CPU=5`, `RLIMIT_AS=512MB`, `RLIMIT_NOFILE=16`, `signal.alarm=10s` are load-bearing — relaxing any cap is an ACE risk. Requires documented security review.
  3. **[A] YOU MUST report Cohen's Kappa alongside M7 scores.** Never average two judges silently when they disagree beyond the per-axis threshold. Honest-numbers contract.
- **Verdict bar** — DEPLOY / HOLD / FAIL thresholds per Layer 8's table (plugin-specific section).
- **State paths** — runtime (gitignored): `plugins/lich-core/state/`, `plugins/lich-sandbox/state/run-log.jsonl`, `plugins/lich-preference/state/{learnings.json,overrides.json}`, `plugins/lich-rubric/state/kappa-log.jsonl`, `plugins/lich-verdict/state/verdict.jsonl`. Ship-time config (committed): `plugins/lich-rubric/config/rubric-v1.json`, `plugins/lich-python/config/ruff-rule-map.json`, `plugins/lich-typescript/config/biome-rule-map.json`.
- **Agent tiers** — Opus = M7 disagreement adjudication + cross-engine verdict synthesis; Sonnet = M7 default judge + lich-core analyzer loops; Haiku = M7 budget fallback + rubric-schema freshness audit + M2 structural summarization.
- **Anti-patterns** — duplicating Hydra R3; silent M5 skip on Windows; rule-death from single rejection (M6 floor violation); bare M7 score without Kappa; unbounded sandbox.

### Handoff to Schematic

| Architecture decision | Schematic placeholder | Fill value |
|----------------------|----------------------|-----------|
| Plugin slug | `{{PLUGIN_SLUG}}` | `lich` |
| Display name | `{{PluginName}}` | `Lich` |
| Tagline | `{{PLUGIN_TAGLINE}}` | `Code review for AI-assisted development — catches runtime failures, learns your preferences, judges quality honestly.` |
| One-line purpose | `{{PLUGIN_ONE_LINE_PURPOSE}}` | `Answers "Is this code good?" via static suspicion + sandboxed confirmation + Bayesian preference learning + LLM rubric judgment.` |
| Game origin | `{{PLUGIN_GAME_ORIGIN}}` | `Hollow Knight — Lich Lords (gate-reviewers)` |
| 5-questions slot | `{{PLUGIN_QUESTION}}` | `Is this code good?` |
| Roadmap phase | `{{PHASE_NUMBER}}` | `3` |
| Plugin index | `{{PLUGIN_INDEX}}` | `6` |
| Engine prefix | `{{ENGINE_PREFIX}}` | `M` |
| Engine count (MVP) | `{{ENGINE_COUNT}}` | `5` (MVP); `7` (full) |
| Defining engine | `{{DEFINING_ENGINE_ID}}` | `M5` (Bounded Subprocess Dry-Run — the novel pipeline) |
| First sub-plugin | `{{SUB_PLUGIN_1_NAME}}` | `lich-core` |
| Sub-plugin count | `{{SUB_PLUGIN_COUNT}}` | `7` + `full` meta |
| Trigger model | `{{TRIGGER_MODEL}}` | `hybrid` (PostToolUse hook + skill-invoked) |
| Events published | `{{EVENT_PUBLISH_LIST}}` | `lich.review.completed, lich.rule.disabled, lich.sandbox.failed` |
| Events subscribed | `{{EVENT_SUBSCRIBE_LIST}}` | `crow.change.classified, hydra.vuln.detected, pech.budget.threshold.crossed, emu.runway.threshold.crossed` |
| Repo URL | `{{REPO_URL}}` | `https://github.com/enchanted-plugins/lich` |
| Plugin home dir | `{{PLUGIN_HOME_DIR}}` | `~/.claude/plugins/lich` |

**Placeholder gaps (new tokens to propose adding to schematic):** none. The architecture's 7-sub-plugin breakdown and 5-axis rubric fit within schematic's existing token set — though the `{{SUB_PLUGIN_2_*}}` through `{{SUB_PLUGIN_7_*}}` tokens aren't defined in schematic yet (schematic currently only enumerates sub-plugin 1 placeholders, per pech-architecture's same observation). Recommendation: extend schematic's vocabulary to `{{SUB_PLUGIN_N_*}}` with a max N of 8.

---

*Generated 2026-04-19. Source prompt: `wixie/prompts/lich-architecture/prompt.xml` v1. Review workflow: `/test-prompt` → `/converge` → dispatch. Next step after this document: execute `/create` pass to fill remaining schematic placeholders into working plugin code.*
