# Coverage contract — what a Lich result actually means

Audience: anyone consuming Lich output, human or agent.

## The law

> **ZERO FINDINGS + INCOMPLETE COVERAGE MUST NEVER MEAN CLEAN.**

Lich reports **coverage** alongside **findings**. An empty finding list is not
evidence of safety unless coverage says the analysis was complete.

## Why this exists

`go.analyze()` used to return `[]` for five different situations, and no
caller could distinguish them:

| situation | old result | new coverage status |
|---|---|---|
| staticcheck not installed | `[]` | `unavailable` |
| staticcheck timed out | `[]` | `degraded` |
| invocation / parse failure | `[]` | `degraded` |
| rule registry unreadable | `[]` | `degraded` |
| genuinely no findings | `[]` | `partial` |

This was not hypothetical. staticcheck needs **8–12 s warm and 40–98 s cold**;
the shipped timeout was **5 s**, so it expired on essentially every real Go
package and the adapter reported `total: 0`. Measured against a file
containing a HIGH-severity `SA5000` nil-map assignment:

```
shipped (5s timeout):    flags 0   — indistinguishable from clean
repaired (120s):         flags 1   — [SA5000 HIGH]
repaired, forced 5s:     flags 0   — coverage DEGRADED, false_clean_risk true
```

## Language truth

| language | engine | what actually runs |
|---|---|---|
| Python | native | stdlib `ast` M1 walker (intervals, nullability, container shape) + optional ruff |
| Go | adapter | `staticcheck` subprocess. **No interval engine.** Findings are exactly staticcheck's |
| Rust / Java / C++ / Ruby / Shell | adapter | external analyzer subprocess |
| any | `lich-review` skill | **LLM reasoning, not an executed parser** — the skill's own SKILL.md says so |

A genuine clean Go run reports `partial`, never `complete`, because the
adapter routes only the `correctness_m1` bucket and drops unmapped rule IDs.
It under-covers by design and now says so.

## Security lane: capability, not branding

The Go adapter drops `security_defer_to_hydra` rules. That deferral used to be
unconditional, justified by "Hydra R3 owns CWEs" — an ownership claim, not a
capability claim.

Measured failure: on a Go CLI fixture, Hydra's injection rule required an HTTP
taint token on the sink line and could not fire, while Lich refused to look
because the lane was "owned". A confirmed command injection fell into the gap
between two tools that each believed the other had it.

`handoff.py` now requires evidence. A lane is reported covered **only** when a
Hydra `enchanter.analysis-report/v1` document shows `complete`, untruncated
coverage of that class for that file:

```bash
export LICH_HYDRA_REPORT_DIR=/path/to/hydra-reports
```

Absent, partial, degraded or truncated evidence all yield **UNCOVERED**, which
is surfaced rather than silently dropped.

## Reading the output

```json
{
  "total": 0,
  "analysis_status": "degraded",
  "false_clean_risk": true,
  "clean": false,
  "coverage": [ { "class": "...", "status": "...", "notes": "..." } ]
}
```

Branch on **`clean`**, never on `total == 0`. If `false_clean_risk` is true,
the uncovered classes are your responsibility to review by other means.

## Tuning

| variable | default | meaning |
|---|---|---|
| `LICH_STATICCHECK_TIMEOUT_S` | `120` | staticcheck wall-clock budget. Expiry is reported as `degraded`, never as zero findings. |
| `LICH_HYDRA_REPORT` | unset | path to one Hydra analysis report |
| `LICH_HYDRA_REPORT_DIR` | unset | directory of Hydra reports, matched by target filename |
