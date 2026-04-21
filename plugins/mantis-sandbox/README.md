# mantis-sandbox

> *M5 Bounded Subprocess Dry-Run — turn M1's static suspicions into confirmed runtime failures, or retire them as false positives. Python stdlib only. Zero deps.*

## Why this exists

Static analyzers over-report. A div-zero flag on `sum(xs) / len(xs)` is technically correct and operationally noise: is `xs` ever empty at *this* call site? Six existing reviewers will tell you "maybe." None of them will run the function with `xs=[]` and show you the `ZeroDivisionError`.

Dynamic confirmation usually requires root, a Docker daemon, a cloud sandbox, or a fixture corpus the team has to maintain. Those costs are why nobody ships confirmation at plugin weight.

M5 is the middle ground. A ~60-line resource-capped subprocess fence runs the flagged function with one synthesized boundary witness. Either the bug surfaces with a real traceback, or it doesn't. No mock. No fixture corpus. The developer's repo already has the code; M5 only adds the fence.

Confirmed bugs are facts, not probabilities. A confirmed `ZeroDivisionError` at `flag_ref.function` with witness `{"args":[[]]}` is a hard FAIL in the verdict composer (CLAUDE.md § Behavioral contract 5) — never averaged with rubric scores.

## The 60-second mental model

```
  M1 flag  ->  witness synth  ->  RLIMIT + alarm subprocess  ->  traceback match  ->  run-log record
 (static)       (boundary)        (6 caps, scrubbed env)        (flag_class        (confirmed-bug /
                                                                  correspondence)    timeout / no-bug)
```

Invariants:

- One subprocess per witness. No shared state across runs.
- Caps installed in `preexec_fn` between `fork()` and `exec()`.
- Per-run `tempfile.mkdtemp()` cwd, deleted on exit.
- Scrubbed env: no proxies, pinned `PATH=/usr/bin:/bin`, UTF-8 locale only.
- 1 MB per-stream cap on child stdout/stderr; truncation, not fail.
- Parent fences the child with `timeout=SIGNAL_ALARM_SEC + 5` as backstop.

## The six caps (what each one prevents)

Values are literal from `scripts/limits.py`. These are the ACE-risk mitigation for executing developer code on every PR.

| Cap | Value | Prevents |
|---|---|---|
| `RLIMIT_CPU` | 5 s | CPU-bound infinite loops (BFS explosion, cryptographic accident) |
| `RLIMIT_AS` | 512 MB | OOM from unbounded list-build, `x * 10**9` accidental allocation |
| `RLIMIT_NOFILE` | 16 | File-descriptor exhaustion (loop opening files, leaked sockets) |
| `RLIMIT_FSIZE` | 10 MB | Disk-fill DoS (log spam, accidental `dd`-to-file) |
| `RLIMIT_NPROC` | 0 | Fork bombs — `subprocess.Popen` loop, `multiprocessing.Pool` |
| `signal.alarm` | 10 s | Blocked I/O (socket read on dead connection) `RLIMIT_CPU` cannot see |

Relaxing any value converts the sandbox into arbitrary-code-execution-on-every-PR. The contract requires a documented security review before the number changes. See CLAUDE.md § Behavioral contract 2.

Soft and hard limits are set identical — no headroom for escalation inside the child.

## Outcome taxonomy

Every run records exactly one of six statuses to `state/run-log.jsonl`. No binary success/fail collapse.

| Status | Meaning |
|---|---|
| `confirmed-bug` | Witness reproduced the exact `flag_class`-matching exception. Hard FAIL trigger. |
| `timeout-without-confirmation` | `SIGALRM` fired before the bug surfaced. HOLD trigger — could be unreachable, or a separate hang. |
| `no-bug-found` | Child exited 0, or exited with an unrelated exception. Static flag stands; no dynamic confirmation. |
| `input-synthesis-failed` | No type-valid witness could be constructed. Reported honestly; not a pass. |
| `sandbox-error` | Infra failure (SIGKILL from AS/CPU cap, SIGXFSZ, runner exception). Not a review finding. |
| `platform-unsupported` | Host lacks `resource` and no WSL bridge available. Verdict notes M5 did not run. |

The flag_class -> expected exception correspondence is narrow on purpose: a `ZeroDivisionError` only confirms a `div-zero` flag, not an `index-oob` one. If the child raises something unrelated, v1 returns `no-bug-found` and lets M1 reflag on the next pass under the correct rule.

## Example: the div-zero confirmation flow

Buggy target (`repo/stats.py`):

```python
def average(nums):
    return sum(nums) / len(nums)
```

M1 flags line 2 with `flag_class="div-zero"` and `witness_hints={"divisor_name": "nums"}`. Witness synthesis produces `{"args": [[]]}` as the boundary input. M5 runs the child:

```bash
$ python plugins/mantis-sandbox/scripts/sandbox.py
{"confirmed": 1, "timeout": 0, "sandbox_error": 0, "no_bug": 0, "synth_failed": 0, "unsupported": 0}
```

The run-log record:

```json
{
  "ts": "2026-04-20T14:23:11.482+00:00",
  "flag_ref": {"file": "repo/stats.py", "function": "average", "flag_class": "div-zero", "line": 2},
  "witness": {"args": [[]]},
  "status": "confirmed-bug",
  "exit_code": 1,
  "signal": null,
  "error_class": "ZeroDivisionError",
  "traceback_head": "Traceback (most recent call last):\n  File \"_sandbox_child.py\", ...\nZeroDivisionError: division by zero",
  "duration_ms": 47,
  "backend": "posix"
}
```

That record is a fact, not a score. The verdict composer reads it and emits FAIL. The developer sees the exact input that breaks the function, not a ruling about severity.

## Running it

Seed flags from M1:

```bash
python plugins/mantis-core/scripts/__main__.py <target_file_or_dir>
# writes plugins/mantis-core/state/review-flags.jsonl
```

Confirm with M5:

```bash
python plugins/mantis-sandbox/scripts/sandbox.py
# reads the default input path, writes plugins/mantis-sandbox/state/run-log.jsonl
# prints JSON summary: {"confirmed": N, "timeout": N, ...}
```

Custom paths:

```bash
python plugins/mantis-sandbox/scripts/sandbox.py my-flags.jsonl my-log.jsonl
```

Install via the marketplace:

```bash
/plugin install mantis-sandbox@mantis
# or the bundle: /plugin install full@mantis
```

`mantis-core` must produce the flags file; install both, or use `full`.

## Platform reality

- **Linux / macOS / native POSIX:** full sandbox. All 6 caps + `signal.alarm` active.
- **Windows with WSL:** bridged via `scripts/bridge/wsl.py`. Child runs under `wsl.exe -e env -i python3`; Windows paths translate to `/mnt/<letter>/...`; same 6 caps apply inside WSL. Signal detection is stderr-marker based (`Alarm clock`, `Killed`, `File size limit exceeded`) rather than negative returncode.
- **Windows without WSL:** `platform-unsupported` emitted per flag. Not silent; the verdict notes M5 did not run and falls back to M1-only judgment with reduced confidence.
- **Node / TypeScript runner (`scripts/runners/node.py`):** weaker sandbox. Heap cap via `--max-old-space-size=512` and a 10 s parent timeout, but **no equivalents for `RLIMIT_NOFILE`, `RLIMIT_FSIZE`, `RLIMIT_NPROC`**. Documented honestly on the tin. `.ts` / `.tsx` targets require `tsx` or `ts-node` in the environment; absent that, the runner returns `input-synthesis-failed` rather than pretending it transpiled.

## Why this is the moat

No existing reviewer ships static-suspicion -> sandboxed-confirmation at zero-dep plugin weight. Semgrep and ESLint are static-only. CodeQL needs a build extraction step. Infer needs Clang and a compile database. Pyre is type-checking, not runtime. Every alternative either stops at the flag (static tools) or demands infrastructure the reviewer does not own (Docker, cloud sandbox, CI runner). M5 runs in a `preexec_fn` between `fork` and `exec`, on the developer's machine, with six stdlib caps and no config — and that contract, "confirmed bugs are facts, not probabilities," comes from actually running the code, not from scoring it.

## Non-duplication contract

M5 does not:

- Scan for CWEs. That is Reaper's R3 lane — 98 CWEs across 2,011 patterns. M5 boosts M6 attention weight on Reaper-flagged files but never reclassifies.
- Classify changes. That is Hornet's V1/V2 lane. M5 consumes Hornet's trust score into the M6 prior.
- Emit verdicts. That is `mantis-verdict`'s lane. M5 writes `run-log.jsonl` records; the verdict composer reads them.
- Persist witness outputs. The per-run tempdir is ephemeral. Only the run-log record survives.

Breaking the split fractures severity source-of-truth across plugins.

## State files

| File | Schema keys | Purpose |
|---|---|---|
| `state/run-log.jsonl` | `ts`, `flag_ref`, `witness`, `status`, `exit_code`, `signal`, `error_class`, `traceback_head`, `duration_ms`, `backend` | Append-only record, one line per witness run. |

`traceback_head` is bounded at 1000 chars. `backend` is `posix` / `wsl` / `unsupported`. The record shown in the div-zero example above is the canonical shape.

## Security

This plugin executes developer code. The six resource caps are the only thing between a PR's payload and the host. Any cap relaxation requires a documented security review (CLAUDE.md § Behavioral contract 2).

## Next

- [Sandbox demo walkthrough](../../tests/demo/sandbox_demo.sh)
- [mantis-core README](../mantis-core/README.md) — M1 static pass that feeds M5
- [mantis-verdict README](../mantis-verdict/README.md) — composes M1 + M5 + M6 + M7 into DEPLOY / HOLD / FAIL
