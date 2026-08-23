---
name: lich-review
description: >
  Triages M1 Cousot Interval Propagation and M2 Falleri Structural Diff on a
  code scope via LLM reasoning over the source (Read/Grep/Glob only — this
  skill has no Bash and does not itself execute a parser), flags
  runtime-failure candidates (div-zero, null deref, OOB, overflow, resource
  leak), and hands the flagged sites to lich-sandbox for M5 confirmation,
  which is the stage that actually executes code. Use when: the user runs
  /lich-review on a hunk / file / PR, or the PostToolUse hook fires on
  Write/Edit/MultiEdit. Do not use for: security-taint review (Hydra R3
  owns that), change classification (Crow V1/V2 owns that), rubric-style
  judgment (lich-rubric skill owns that), or anything requiring real code
  execution (lich-sandbox's M5 owns that).
model: sonnet
tools: [Read, Grep, Glob]
---

# lich-review

## Execution model — read this before trusting M1/M2 output

This skill's tool whitelist is `[Read, Grep, Glob]` — **no Bash**. It cannot
invoke `ast.parse`, `tsc --generateTrace`, or any subprocess. Every M1/M2
"flag" below is the reviewing LLM manually reasoning through interval,
nullability, and structural-diff logic while reading source text — a
triage/simulation of the algorithm, not a real interpreter or parser run.
Treat M1/M2 output as a prioritized hypothesis list, never as a soundness
guarantee. The only stage in the pipeline that genuinely executes code is
**lich-sandbox's M5** (`tools: [Read, Bash]`), which is why every
`needs_M5_confirmation: true` flag must go through it before it counts as
confirmed.

## Preconditions

- A `lich-core` sub-plugin state dir exists at `plugins/lich-core/state/`.
- Target code is readable in the detected language. This skill has no
  execution tools, so "parsing" here means the LLM reading the file with
  `Read` and reasoning about its structure — not literally running `ast`
  (Python) or `tsc --generateTrace` (TypeScript). If the file is unreadable
  or the language is unsupported, emit `substrate-parse-failed` and skip —
  do not fabricate flags.
- Crow's `change-tracker/state/audit.jsonl` is optionally present. If absent, Lich runs on the full file instead of Crow-flagged hunks.

## Inputs

- **Slash command**: `/lich-review <scope>` where scope is `hunk` (current file + line range), `file` (full file path), or `pr` (all changes in the PR).
- **Hook payload**: PostToolUse event with `tool`, `file_path`, `old_string`, `new_string`.

## Steps

1. **Read the target.** Read the file(s) with the `Read` tool. There is no Bash here, so there is no literal `ast.parse` or `tsc --noEmit --generateTrace` call — reading fails only if the file is missing/unreadable. On that failure, emit `substrate-parse-failed` and return empty flags.
2. **Triage M1 Cousot Interval Propagation.** By LLM reasoning over the read source (not an executed AST walk), mentally apply interval + nullability + container-shape abstract domains, widening judgment at roughly N=3 iterations with language-aware bounds (`{0, 1, -1, sys.maxsize}` for Python ints). Emit per-site flags: `{file, line, variable, abstract_value, failure_class, severity}`. These are triage hypotheses, not a proof — only lich-sandbox's M5 subprocess run can confirm one.
3. **Triage M2 Falleri Structural Diff** *if comparing two versions*, again by LLM reasoning rather than running GumTree — approximate the same conservative intent (`min_height=3`, `min_dice=0.6`, `min_similarity=0.7`) when judging whether an edit is a move/rename vs. a semantic change. If the diff is too large to reason over confidently, fall back to describing it as a unified diff and flag `structural-diff-timeout`.
4. **Read Hydra's vuln-detector audit.jsonl.** If a CRITICAL or HIGH CWE finding exists on the target file, boost the review-attention weight for M6's prioritization and annotate M7's rubric input with "Security context: Hydra flagged {cwe} {severity}". Never re-scan for the CWE itself.
5. **Emit flagged sites.** Write to `plugins/lich-core/state/review-flags.jsonl` for lich-sandbox to pick up. Fields: `{ts, file, line, failure_class, severity, M1_confidence, needs_M5_confirmation}`.

## Outputs

- `plugins/lich-core/state/review-flags.jsonl` — append-only flag records.
- stderr: short summary of M1 findings count, M2 edits detected, Hydra context applied.
- Return value to parent: a JSON block `{flags: [...], M2_edits: N, duration_ms: X}`.

## Handoff

Next skill in the chain: **lich-sandbox** (SKILL at `plugins/lich-sandbox/skills/lich-sandbox/SKILL.md`) — confirms each flagged site via bounded subprocess dry-run.

After both skills run, **lich-verdict** composes the final DEPLOY/HOLD/FAIL verdict.

## Failure modes

When this skill fails, log to `state/learnings.json` with one of the codes from `@../vis/packages/core/conduct/failure-modes.md`:

- **F02 fabrication** — if M1 reports a flag the source text doesn't actually support, or reports it with a confidence that implies real execution rather than LLM triage (never do this; soundness is the brand)
- **F04 task drift** — if the skill strays into security-taint detection (Hydra's lane)
- **F13 distractor pollution** — if M2 emits > 20 edits on a small diff; revert to conservative parameters
- **F14 version drift** — if lich-typescript's actual `tsc` execution (which this triage skill does not perform) reports an unsupported version; emit a compatibility warning
