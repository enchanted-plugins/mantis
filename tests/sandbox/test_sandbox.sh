#!/usr/bin/env bash
# Mantis M5 integration harness — exercises the M1 -> M5 pipeline end to end
# across six fixture files covering all canonical run-log statuses.
#
# Stages:
#   1. Run M1 walker (Agent 2) across every fixture.
#   2. Supplement with hand_flags.jsonl if M1 underproduced.
#   3. Invoke the sandbox orchestrator (Agent 3's sandbox.py).
#   4. Assert outcomes via check_outcomes.py.
#
# Honest platform handling: on Windows without WSL the orchestrator
# emits `platform-unsupported` for every flag — check_outcomes.py sees
# that and exits 0 with `PLATFORM SKIP`. The presence of a record per
# fixture is still verified so silent drops fail the harness.

set -uo pipefail

# -----------------------------------------------------------------------------
# Bash 3.2 (macOS default) and bash-on-Windows both honour BASH_SOURCE.
# Resolve REPO_ROOT two levels up from this script.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

FIXTURES_DIR="tests/sandbox/fixtures"
HAND_FLAGS="${FIXTURES_DIR}/hand_flags.jsonl"
M1_LOG="plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="plugins/mantis-sandbox/state/run-log.jsonl"
M1_ENTRY="plugins/mantis-core/scripts/__main__.py"
M5_ENTRY="plugins/mantis-sandbox/scripts/sandbox.py"
CHECK="tests/sandbox/check_outcomes.py"

PYTHON="${PYTHON:-python}"
EXPECTED_FIXTURES=6

echo "[harness] repo root: ${REPO_ROOT}"
echo "[harness] python:    $(${PYTHON} --version 2>&1)"

# -----------------------------------------------------------------------------
# Pre-flight: confirm dependency scripts exist. Missing = dependency not ready,
# not a test failure. Exit 2 for "deps missing" so CI can tell it apart.
if [[ ! -f "${M1_ENTRY}" ]]; then
    echo "[harness] FAIL: M1 walker entrypoint missing: ${M1_ENTRY}" >&2
    echo "[harness]        Agent 2 slice not ready — cannot stage flags." >&2
    exit 2
fi

if [[ ! -f "${CHECK}" ]]; then
    echo "[harness] FAIL: check_outcomes.py missing: ${CHECK}" >&2
    exit 2
fi

if [[ ! -f "${HAND_FLAGS}" ]]; then
    echo "[harness] FAIL: hand_flags.jsonl missing: ${HAND_FLAGS}" >&2
    exit 2
fi

# -----------------------------------------------------------------------------
# Stage 1: M1 walker across every fixture. Rubric is "best effort" — a
# missing or crashing walker does not abort the pipeline; we fall back to
# hand_flags.jsonl at Stage 2.
echo "[harness] stage 1: running M1 walker across ${FIXTURES_DIR}/*.py"
mkdir -p "$(dirname "${M1_LOG}")"
rm -f "${M1_LOG}"

walker_fail=0
for f in "${FIXTURES_DIR}"/*.py; do
    [[ -f "$f" ]] || continue
    if ! "${PYTHON}" "${M1_ENTRY}" "$f" >/dev/null 2>&1; then
        walker_fail=$((walker_fail + 1))
    fi
done
if [[ "${walker_fail}" -gt 0 ]]; then
    echo "[harness]          M1 walker reported failure on ${walker_fail} fixture(s) (non-fatal)"
fi

# -----------------------------------------------------------------------------
# Stage 2: supplement with hand-crafted flags if we did not reach the
# fixture-coverage target. Count unique file paths in the log — one flag
# per fixture is the floor we need for the orchestrator to dispatch.
unique_fixture_count() {
    # Zero-argument function: count distinct `file` values in M1_LOG.
    if [[ ! -s "${M1_LOG}" ]]; then
        echo 0
        return
    fi
    "${PYTHON}" - "${M1_LOG}" <<'PY'
import json, sys
seen = set()
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            seen.add(json.loads(line).get("file"))
        except json.JSONDecodeError:
            continue
print(len(seen))
PY
}

present=$(unique_fixture_count)
echo "[harness] stage 2: ${present}/${EXPECTED_FIXTURES} fixtures flagged by M1"
if [[ "${present}" -lt "${EXPECTED_FIXTURES}" ]]; then
    echo "[harness]          supplementing from ${HAND_FLAGS}"
    # Append only rows whose `file` is not already present in M1_LOG — the
    # synth_failed.py + timeout_infinite.py flags are the ones M1 v1 cannot
    # produce; everything else is deduplicated.
    "${PYTHON}" - "${M1_LOG}" "${HAND_FLAGS}" <<'PY'
import json, sys
existing = set()
log_path, hand_path = sys.argv[1], sys.argv[2]
try:
    with open(log_path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                existing.add(json.loads(line).get("file"))
            except json.JSONDecodeError:
                continue
except FileNotFoundError:
    pass
with open(hand_path, "r", encoding="utf-8") as fh, \
     open(log_path, "a", encoding="utf-8") as out:
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if rec.get("file") in existing:
            continue
        out.write(json.dumps(rec) + "\n")
PY
    present=$(unique_fixture_count)
    echo "[harness]          after supplement: ${present}/${EXPECTED_FIXTURES}"
fi

if [[ "${present}" -lt "${EXPECTED_FIXTURES}" ]]; then
    echo "[harness] FAIL: flag coverage still below ${EXPECTED_FIXTURES} after supplement" >&2
    exit 1
fi

# -----------------------------------------------------------------------------
# Stage 3: invoke sandbox orchestrator. Absent entrypoint is a "deps not
# ready" signal (exit 2), not a harness logic failure.
echo "[harness] stage 3: running M5 sandbox orchestrator"
mkdir -p "$(dirname "${M5_LOG}")"
rm -f "${M5_LOG}"

if [[ ! -f "${M5_ENTRY}" ]]; then
    echo "[harness] SKIP (deps not ready): sandbox orchestrator missing: ${M5_ENTRY}" >&2
    echo "[harness]        Agent 3 slice not integrated — harness validated against empty state." >&2
    # Still run check_outcomes to prove it degrades cleanly on empty log.
    "${PYTHON}" "${CHECK}" --allow-empty
    exit 2
fi

# Pass explicit input + output paths so the harness does not depend on the
# orchestrator's repo-root resolution (which differs across dev checkouts).
if ! "${PYTHON}" "${M5_ENTRY}" "${M1_LOG}" "${M5_LOG}"; then
    echo "[harness]          orchestrator returned non-zero (may still have produced records)"
fi

# -----------------------------------------------------------------------------
# Stage 4: outcome assertions.
echo "[harness] stage 4: asserting outcomes via ${CHECK}"
"${PYTHON}" "${CHECK}"
rc=$?
if [[ $rc -eq 0 ]]; then
    echo "[harness] PASS"
else
    echo "[harness] FAIL (exit ${rc})"
fi
exit $rc
