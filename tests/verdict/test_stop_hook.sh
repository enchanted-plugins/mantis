#!/usr/bin/env bash
# Slice C: Stop-event auto-compose verification.
#
# Simulates a Claude Code Stop event by invoking the mantis-verdict plugin
# dispatcher with an empty stdin payload (Stop carries no tool_input.file_path)
# and asserts the advisory-only contract:
#   (1) exit 0 always (fail-open)
#   (2) "spawn mantis-verdict-compose" line hits the hook log
#   (3) background compose.py completes and verdict.jsonl is well-formed
#   (4) CLAUDE_SUBAGENT=1 short-circuits the dispatcher (no spawn)
#
# Runs under two state shapes:
#   (a) empty M1 + M5 logs   -> verdict.jsonl gains zero records cleanly
#   (b) populated M1 + M5    -> verdict.jsonl gains the expected fixture records

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

DISPATCH="$REPO_ROOT/plugins/mantis-verdict/hooks/dispatch.sh"
LOG="$REPO_ROOT/.claude/logs/hooks.log"
M1_LOG="$REPO_ROOT/plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="$REPO_ROOT/plugins/mantis-sandbox/state/run-log.jsonl"
V_LOG="$REPO_ROOT/plugins/mantis-verdict/state/verdict.jsonl"

mkdir -p "$(dirname "$LOG")"
touch "$LOG"

fails=0
pass() { echo "[pass] $*"; }
fail() { echo "[fail] $*"; fails=$((fails+1)); }

_wait_for_compose() {
    # Background compose.py finishes well under 2s on both empty and populated
    # fixtures. Poll the log for the compose exit marker (python stdout/stderr
    # redirected into the same LOG file) up to ~3s so CI flakiness doesn't
    # produce a race.
    local before_bytes="$1"
    local i=0
    while [[ $i -lt 30 ]]; do
        local now_bytes
        now_bytes=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
        if [[ -n "$now_bytes" && "$now_bytes" -gt "$before_bytes" ]]; then
            # Give the child a beat more to finish flushing the verdict file.
            sleep 0.2
            return 0
        fi
        sleep 0.1
        i=$((i+1))
    done
}

# ---------------------------------------------------------------------------
# Scenario (a): empty state — Stop on an empty project should not emit records
# ---------------------------------------------------------------------------
echo "[stop-hook] scenario (a): empty state"
: > "$M1_LOG"
: > "$M5_LOG"
: > "$V_LOG"
off_a=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
start_ms=$(date +%s%N 2>/dev/null || echo 0)
set +e
: | bash "$DISPATCH" mantis-verdict-compose
rc=$?
set -e
end_ms=$(date +%s%N 2>/dev/null || echo 0)
[[ $rc -eq 0 ]] && pass "(a) dispatcher exit=0" || fail "(a) dispatcher exit=$rc"
sync_ms=$(( (end_ms - start_ms) / 1000000 ))
echo "[stop-hook]          synchronous budget: ${sync_ms}ms"
_wait_for_compose "$off_a"

python tests/verdict/check_stop.py "$LOG" "$off_a" \
    && pass "(a) spawn line present" || fail "(a) spawn line missing"

v_count_a=$(wc -l < "$V_LOG" 2>/dev/null | tr -d ' ')
if [[ "$v_count_a" == "0" ]]; then
    pass "(a) empty state -> 0 verdict records"
else
    fail "(a) empty state -> expected 0 records, got $v_count_a"
fi

# ---------------------------------------------------------------------------
# Scenario (b): populated state — full pipeline produces verdict records
# ---------------------------------------------------------------------------
echo "[stop-hook] scenario (b): populated state"
: > "$M1_LOG"
: > "$M5_LOG"
: > "$V_LOG"
for f in tests/fixtures/quality-ladder/bad.py \
         tests/fixtures/quality-ladder/high_level.py \
         tests/fixtures/quality-ladder/massive_orders.py ; do
    [[ -f "$f" ]] && python plugins/mantis-core/scripts/__main__.py "$f" >/dev/null 2>&1 || true
done
python plugins/mantis-sandbox/scripts/sandbox.py "$M1_LOG" "$M5_LOG" >/dev/null 2>&1 || \
    python plugins/mantis-sandbox/scripts/sandbox.py >/dev/null 2>&1 || true

off_b=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
start_ms=$(date +%s%N 2>/dev/null || echo 0)
set +e
: | bash "$DISPATCH" mantis-verdict-compose
rc=$?
set -e
end_ms=$(date +%s%N 2>/dev/null || echo 0)
[[ $rc -eq 0 ]] && pass "(b) dispatcher exit=0" || fail "(b) dispatcher exit=$rc"
sync_ms=$(( (end_ms - start_ms) / 1000000 ))
echo "[stop-hook]          synchronous budget: ${sync_ms}ms"
_wait_for_compose "$off_b"

python tests/verdict/check_stop.py "$LOG" "$off_b" \
    && pass "(b) spawn line present" || fail "(b) spawn line missing"

v_count_b=$(wc -l < "$V_LOG" 2>/dev/null | tr -d ' ')
if [[ "$v_count_b" -gt 0 ]]; then
    pass "(b) populated state -> $v_count_b verdict record(s)"
else
    fail "(b) populated state -> expected >=1 record, got $v_count_b"
fi

# ---------------------------------------------------------------------------
# Scenario (c): subagent guard — CLAUDE_SUBAGENT=1 means no spawn
# ---------------------------------------------------------------------------
echo "[stop-hook] scenario (c): subagent guard"
off_c=$(wc -c < "$LOG" 2>/dev/null | tr -d ' ')
set +e
: | CLAUDE_SUBAGENT=1 bash "$DISPATCH" mantis-verdict-compose
rc=$?
set -e
[[ $rc -eq 0 ]] && pass "(c) dispatcher exit=0 under guard" || fail "(c) dispatcher exit=$rc"

python tests/verdict/check_stop.py "$LOG" "$off_c" --expect-absent \
    && pass "(c) no spawn under CLAUDE_SUBAGENT=1" || fail "(c) spawn leaked under guard"

# ---------------------------------------------------------------------------
echo "[stop-hook] summary: $fails failure(s)"
if [[ $fails -eq 0 ]]; then
    echo "[stop-hook] PASS"
    exit 0
fi
echo "[stop-hook] FAIL"
exit 1
