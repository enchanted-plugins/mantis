#!/usr/bin/env bash
# End-to-end event-bus harness.
#
# Verifies:
#   1. `/mantis-disable` publishes `mantis.rule.disabled` to the bus.
#   2. The verdict composer publishes `mantis.review.completed` per file.
#   3. An injected `hornet.change.classified` is visible via
#      subscriptions.check_for_hornet_boost.
#
# The bus is brand invariant #7 — observability. Every publisher is
# wrapped try/except in the production code, so a bus failure must never
# break compose/override/sandbox. This harness asserts the bus *does*
# work when shared/events is present.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-python}"
BUS="${REPO_ROOT}/shared/events/bus.jsonl"
OVERRIDES="${REPO_ROOT}/plugins/mantis-preference/state/overrides.json"
VERDICT_LOG="${REPO_ROOT}/plugins/mantis-verdict/state/verdict.jsonl"
M1_LOG="${REPO_ROOT}/plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="${REPO_ROOT}/plugins/mantis-sandbox/state/run-log.jsonl"

echo "[harness] repo:   ${REPO_ROOT}"
echo "[harness] python: $(${PYTHON} --version 2>&1)"

# ---------------------------------------------------------------------
# Stage 0: clear bus.jsonl and overrides.json
# ---------------------------------------------------------------------
mkdir -p "$(dirname "${BUS}")"
: > "${BUS}"
: > "${OVERRIDES}"
: > "${VERDICT_LOG}"
: > "${M1_LOG}"
: > "${M5_LOG}"

# ---------------------------------------------------------------------
# Stage 1: override -> mantis.rule.disabled
# ---------------------------------------------------------------------
echo "[harness] stage 1: /mantis-disable publishes mantis.rule.disabled"
"${PYTHON}" plugins/mantis-preference/scripts/override.py \
    --dev alice --rule PY-M1-001 disable >/dev/null

rule_events=$("${PYTHON}" - "${BUS}" <<'PY'
import json, sys
path = sys.argv[1]
count = 0
with open(path, "r", encoding="utf-8") as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        if rec.get("topic") == "mantis.rule.disabled":
            count += 1
print(count)
PY
)
if [[ "${rule_events}" -lt 1 ]]; then
    echo "[harness] FAIL: expected >= 1 mantis.rule.disabled event, got ${rule_events}" >&2
    exit 1
fi
echo "[harness]          got ${rule_events} mantis.rule.disabled event(s)"

# ---------------------------------------------------------------------
# Stage 2: compose verdict on a clean fixture -> mantis.review.completed
# ---------------------------------------------------------------------
echo "[harness] stage 2: verdict composer publishes mantis.review.completed"
"${PYTHON}" plugins/mantis-verdict/scripts/compose.py \
    --file tests/fixtures/quality-ladder/high_level.py >/dev/null

review_events=$("${PYTHON}" - "${BUS}" <<'PY'
import json, sys
path = sys.argv[1]
count = 0
with open(path, "r", encoding="utf-8") as fh:
    for ln in fh:
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)
        if rec.get("topic") == "mantis.review.completed":
            count += 1
print(count)
PY
)
if [[ "${review_events}" -lt 1 ]]; then
    echo "[harness] FAIL: expected >= 1 mantis.review.completed event, got ${review_events}" >&2
    exit 1
fi
echo "[harness]          got ${review_events} mantis.review.completed event(s)"

# ---------------------------------------------------------------------
# Stage 3: inject a synthetic hornet.change.classified event and confirm
# check_for_hornet_boost returns the trust score.
# ---------------------------------------------------------------------
echo "[harness] stage 3: check_for_hornet_boost returns the synthetic trust"
"${PYTHON}" - "${REPO_ROOT}" <<'PY'
import sys
from pathlib import Path
repo = Path(sys.argv[1])
sys.path.insert(0, str(repo / "shared"))
from events.bus import publish
from events.subscriptions import check_for_hornet_boost

publish("hornet.change.classified",
        {"file": "src/foo.py", "trust": 0.62,
         "classification": "refactor"},
        source="hornet")
assert check_for_hornet_boost("src/foo.py") == 0.62, \
    "expected 0.62 trust score"
assert check_for_hornet_boost("src/other.py") is None, \
    "expected None for unmatched file"
print("OK")
PY
rc=$?
if [[ ${rc} -ne 0 ]]; then
    echo "[harness] FAIL: check_for_hornet_boost assertion failed" >&2
    exit 1
fi

# ---------------------------------------------------------------------
# Stage 4: schema — every persisted line has the 5 required fields.
# ---------------------------------------------------------------------
echo "[harness] stage 4: every persisted line validates the schema"
"${PYTHON}" - "${BUS}" <<'PY'
import json, sys
required = {"topic", "payload", "ts", "source", "uuid"}
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    for i, ln in enumerate(fh, 1):
        ln = ln.strip()
        if not ln:
            continue
        rec = json.loads(ln)  # raises on malformed JSON
        missing = required - set(rec.keys())
        assert not missing, f"line {i}: missing fields {missing}"
print("OK")
PY
rc=$?
if [[ ${rc} -ne 0 ]]; then
    echo "[harness] FAIL: schema validation failed" >&2
    exit 1
fi

echo "[harness] PASS"
exit 0
