# Getting started with Lich

Lich is code review for AI-assisted development: static suspicion, sandboxed confirmation, Bayesian per-developer preference weighting, rubric judgment. Its target: runtime bugs that compile-time checks miss, minus the style-noise that collapses signal-to-noise over time. This page gets you from zero to a first review in under 5 minutes.

> Pre-release status: Lich is Phase 3 #6 in the ecosystem and has not shipped a public release yet. The command shape and engine IDs below are the committed public surface; the numbers in demo scenarios come from the README's worked example.

## 1. Install (60 seconds)

Once v0.1.0 ships:

```
/plugin marketplace add enchanter-ai/lich
/plugin install full@lich
/plugin list
```

You should see the Lich sub-plugins including `lich-core`, `lich-preference`, `lich-python`, `lich-rubric`, `lich-sandbox`, `lich-typescript`, and `lich-verdict`. If any are missing, see [installation.md](installation.md).

## 2. Run your first review

Open a PR (or stage a change) and run:

```
/lich-review
```

Lich runs its five-engine pipeline in sequence:

1. **M1 Cousot Interval Propagation** — static abstract-interpretation pass. Flags values with unbounded ranges that reach operators with undefined behavior at boundary values (e.g., `x / n` where `n`'s range includes 0).
2. **M2 Falleri Structural Diff** — tree-level diff to distinguish *new semantic behavior* from *refactor*. Suspicion from M1 survives only if M2 confirms the construct is new, not moved.
3. **M5 Bounded Subprocess Dry-Run** — synthesize a fuzzer input for each surviving suspicion, execute in a `resource.setrlimit`-bounded subprocess sandbox, observe the outcome. Suspicion becomes confirmation only if the sandbox crashes or misbehaves.
4. **M6 Bayesian Per-Developer Preference** — your prior belief about what matters gets updated every time you accept / reject / edit a finding. After enough reviews, Lich knows that you care about divide-by-zero and don't care about trailing-whitespace reminders. Signal-to-noise goes up over time instead of collapsing.
5. **M7 Zheng Pairwise Rubric** — score the surviving findings on a pairwise rubric (Robustness, Failure Resilience, Correctness, Maintainability, etc.) and emit a verdict per finding.

## 3. Read the verdict

Output shape (example from the README scenario):

```
Finding #1  result = user_inputs[i] / n
  M1:  n has range [?, ?] — unknown lower bound, possibly zero
  M2:  NEW assignment (not refactored)
  M5:  ZeroDivisionError observed under synthesized input (n=0)
  M7:  Robustness 5/10, Failure Resilience 2/10
  M6:  Prior floor for this developer on divide-by-zero: 0.72
  Verdict: HOLD
```

Verdicts are advisory — Lich reports, you decide. Nothing is blocked automatically.

## 4. The self-learning loop

Accept, reject, or edit each finding. M6 reads the choice as evidence. Over time:

- Findings you consistently accept rise in priority.
- Findings you consistently reject fall below the signal threshold for your account.
- Findings you edit get weighted by the edit distance — full-rewrite = strong "not quite right", one-word fix = strong "correct but imperfect".

There is no global rule-tuning UI. Lich learns per developer, locally.

## 5. Integration with Sylph

When Sylph's `/sylph:pr` opens a PR, Lich findings from the diff are posted on the PR body as a "Lich summary" block. Reviewers see the verdict without needing to invoke Lich separately.

## Next steps

- [docs/architecture/lich-architecture.md](architecture/lich-architecture.md) — derivations for M1, M2, M5, M6, M7.
- [docs/architecture/](architecture/) — auto-generated diagram.
- [README.md](../README.md) § vs Everything Else — honest comparison against Copilot, Cursor, Qodo, and manual review.

Broken first run? → [troubleshooting.md](troubleshooting.md).
