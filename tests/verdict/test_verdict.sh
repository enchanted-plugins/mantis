#!/usr/bin/env bash
# End-to-end pipeline: M1 walker -> M5 sandbox -> verdict compose.
# Platform-agnostic: on Windows/no-WSL, M5 emits platform-unsupported and the
# verdict still applies via M1 alone per CLAUDE.md §2.

set -uo pipefail
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

echo "[harness] repo:   $REPO_ROOT"
echo "[harness] python: $(python --version 2>&1)"

FIXTURES=(
    tests/fixtures/quality-ladder/bad.py
    tests/fixtures/quality-ladder/high_level.py
    tests/fixtures/quality-ladder/massive_orders.py
)

M1_LOG="$REPO_ROOT/plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="$REPO_ROOT/plugins/mantis-sandbox/state/run-log.jsonl"
V_LOG="$REPO_ROOT/plugins/mantis-verdict/state/verdict.jsonl"

: > "$M1_LOG"
: > "$M5_LOG"
: > "$V_LOG"

echo "[harness] stage 1: M1 walker across ${#FIXTURES[@]} fixture(s)"
for f in "${FIXTURES[@]}"; do
    if [[ ! -f "$f" ]]; then
        echo "[harness] skip: $f not found"
        continue
    fi
    python plugins/mantis-core/scripts/__main__.py "$f" 2>/dev/null || true
done
m1_count=$(wc -l < "$M1_LOG" 2>/dev/null | tr -d ' ' || echo 0)
echo "[harness]          M1 wrote $m1_count flag(s)"

echo "[harness] stage 2: M5 sandbox"
python plugins/mantis-sandbox/scripts/sandbox.py "$M1_LOG" "$M5_LOG" 2>/dev/null || \
    python plugins/mantis-sandbox/scripts/sandbox.py 2>/dev/null || true
m5_count=$(wc -l < "$M5_LOG" 2>/dev/null | tr -d ' ' || echo 0)
echo "[harness]          M5 wrote $m5_count run(s)"

echo "[harness] stage 3: verdict compose"
python plugins/mantis-verdict/scripts/compose.py \
    --file "${FIXTURES[0]}" \
    --file "${FIXTURES[1]}" \
    --file "${FIXTURES[2]}"
v_count=$(wc -l < "$V_LOG" 2>/dev/null | tr -d ' ' || echo 0)
echo "[harness]          verdict wrote $v_count record(s)"

echo "[harness] stage 4: assert per-file verdicts"
python tests/verdict/check_verdicts.py
rc=$?
if [[ $rc -eq 0 ]]; then
    echo "[harness] PASS"
else
    echo "[harness] FAIL"
fi
exit $rc
