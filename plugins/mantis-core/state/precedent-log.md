# mantis-core — Precedent Log

Self-observed operational failures for future-self to grep before risky ops in the M1 walker / M2 structural-diff pipeline. Format per `shared/conduct/precedent.md`. Append; never delete without marking `RESOLVED YYYY-MM-DD`.

Consult: grep before composing multi-stage pipelines that pipe M1 flags into another engine's CLI.

---

## 2026-04-21 — M1 walker positional argv crossed sandbox contract

**Command that failed:**
`plugins/mantis-core/scripts/__main__.py <source.py>` piped into `plugins/mantis-sandbox/scripts/sandbox.py <source.py>` (wrong — sandbox.py's argv[1] is the `review-flags.jsonl` input, not the source file).

**Why it failed:**
Hook dispatcher passed the *source file path* as sandbox argv[1] by mistake. M1 wrote flags to `review-flags.jsonl` correctly, but M5 read no flags because argv[1] pointed at the source — silent no-op, not an error.

**What worked:**
Dispatch hook now passes `$M1_LOG` (review-flags.jsonl path) as sandbox argv[1]. Regression coverage: `tests/regression/test_bugs_2026_04_21.sh` bug-1.

**Signal:** when piping from one stage to another, verify argv shape matches the target CLI. Silent no-op on `.jsonl` reads is the canonical tell — flag count drops to 0 with no traceback.

**Tags:** bash, argv, hook, dispatch, m1, m5, pipeline
