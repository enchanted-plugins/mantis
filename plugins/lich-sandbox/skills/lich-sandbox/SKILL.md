---
name: lich-sandbox
description: >
  Runs M5 Bounded Subprocess Dry-Run. For each M1-flagged runtime-failure
  candidate, synthesizes a witness input (boundary values: 0, None, "",
  sys.maxsize, etc.), executes the containing function in a stdlib-only
  sandbox with resource.setrlimit CPU/AS/NOFILE/FSIZE caps + signal.alarm
  wall-clock timeout, and records confirmed / timeout / sandbox-error /
  input-synthesis-failed outcomes. Use when: lich-core emits flags that
  need confirmation, OR the user runs a skill that explicitly invokes M5.
  Do not use for: running untrusted code without caps, running on Windows
  (skip with platform-unsupported), or as a replacement for real test
  suites.
model: sonnet
tools: [Read, Bash]
---

# lich-sandbox

## Preconditions

- **POSIX platform.** Python's `resource` module is absent on Windows — if `platform.system() == "Windows"`, skip with `platform-unsupported` and do not attempt to run code.
- `plugins/lich-core/state/review-flags.jsonl` exists and has records to confirm.
- Target file is in a project Lich can safely execute (the developer's own repo, not a fetched-unknown source).

## Inputs

- **Hook payload** (chained after lich-core): the review-flags records emitted by lich-core.
- **Optional direct call**: `{file, function, line, flag_class}` — run one confirmation.

## Steps

1. **Platform guard.** If not POSIX, emit `{status: "platform-unsupported"}` and exit. Never silently pretend M5 ran.
2. **Read flagged sites.** Load `plugins/lich-core/state/review-flags.jsonl`; select records with `needs_M5_confirmation: true`.
3. **Synthesize witness inputs.** For each flagged variable, assemble boundary values from a language-aware default set:
   - Python: `{0, -1, None, "", [], {}, sys.maxsize, -sys.maxsize}`
   - TypeScript: `{0, -1, null, undefined, "", [], {}, Number.MAX_SAFE_INTEGER}`
   - Use `inspect.signature` (Python) or parsed type annotations (TS) to filter to type-compatible values.
4. **Execute in sandbox.** For each witness, fork a subprocess with:
   - `resource.setrlimit(RLIMIT_CPU, (5, 5))` — 5 CPU-seconds
   - `resource.setrlimit(RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))` — 512MB address space
   - `resource.setrlimit(RLIMIT_NOFILE, (16, 16))` — 16 open file descriptors
   - `resource.setrlimit(RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))` — 10MB per-file write
   - `signal.alarm(10)` — 10-second wall-clock kill
   - Environment scrubbed: no `HTTP_PROXY`, `HTTPS_PROXY`, `no_proxy=*`
   - Per-run `tempfile.mkdtemp()` write-target, deleted on exit
5. **Record outcome.** For each witness-execution, write to `plugins/lich-sandbox/state/run-log.jsonl`:
   - `{status: "confirmed-bug", error_class: "ZeroDivisionError", witness: {...}, ...}` — bug reproduced
   - `{status: "timeout-without-confirmation", duration_ms: 10000, ...}` — alarm fired before bug surfaced
   - `{status: "sandbox-error", error: "...", ...}` — infra failure (not a finding)
   - `{status: "input-synthesis-failed", reason: "...", ...}` — couldn't construct a type-valid witness
   - `{status: "no-bug-found", witnesses_tried: N, ...}` — all witnesses ran clean
6. **Emit sandbox summary.** Return a JSON block to the parent with aggregate counts per status.

## Outputs

- `plugins/lich-sandbox/state/run-log.jsonl` — append-only run history.
- stderr: one line per run status.
- Parent return: `{confirmed: N, timeout: N, sandbox_error: N, no_bug: N, duration_ms: X}`.

## Handoff

Next skill in the chain: **lich-verdict** composes lich-core flags + lich-sandbox confirmations + lich-rubric scores into DEPLOY/HOLD/FAIL.

## Failure modes

- **F06 premature action** — if the skill attempts to execute without setting resource caps. This is an ACE risk; the caps are load-bearing. Fix: refuse to execute, log, escalate.
- **F10 destructive without confirmation** — if the skill attempts to mkdir / write outside the per-run `tempfile.mkdtemp()`. Counter: filesystem write-target is pinned before `subprocess.run`.
- **F14 version drift** — if Python version < 3.8 (missing `resource.setrlimit` features) or platform.system() detection misidentifies. Counter: explicit `platform-unsupported` path.

## Security note

This skill executes developer code. In a malicious-PR scenario, the resource caps are the mitigation — an unbounded sandbox is arbitrary-code-execution on every review. Any relaxation of the caps requires a documented security review. See `../../CLAUDE.md` § Behavioral contract 2.
