# Sandbox Demo

A single-command walkthrough of Mantis's M1 -> M5 -> verdict pipeline on a deliberately-buggy fixture.

## Run it

```bash
bash tests/demo/sandbox_demo.sh
```

Idempotent: state is reset before each run, so re-running produces identical output. Exit code is always 0 — the demo is informational, not a gate.

`NO_COLOR=1 bash tests/demo/sandbox_demo.sh` disables ANSI escapes.

## What you'll see

1. **Stage 0** — the buggy fixture printed with line numbers, 3 bugs arrowed
2. **Stage 1** — M1 walker flags each runtime-failure candidate
3. **Stage 2** — M5 sandbox attempts confirmation (per-flag status markers)
4. **Stage 3** — verdict composer prints per-engine demands + final reasons
5. **Stage 4** — one-line verdict with interpretation paragraph

## Expected final output

### On Linux / macOS / WSL

```
VERDICT: FAIL (confidence: high)

Interpretation: M1 flagged the runtime-failure candidates; M5 attempted
witness execution in a bounded subprocess (caps per CLAUDE.md §2). See the
run-log records above for each confirmation or clearing.
```

Each M5 record is either `CONFIRMED` (witness reproduced the bug), `CLEARED` (no witness hit), or `TIMEOUT` (hung past the 10s wall-clock alarm).

### On Windows without WSL

```
VERDICT: FAIL (confidence: reduced)

Interpretation: M1 alone is sufficient to issue FAIL (>= 3 HIGH flags trip the
verdict bar's hard-fail threshold). M5 would confirm each bug with a concrete
witness input on Linux/macOS/WSL, promoting the confidence from 'reduced' to
'high'. Today's host can't run the sandbox; the pipeline degrades honestly.
```

Every M5 row shows `SKIPPED (platform)` — explicit, not silent.
