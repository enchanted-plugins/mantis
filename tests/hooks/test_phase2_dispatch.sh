#!/usr/bin/env bash
# Phase-2 hook dispatch wiring test.
#
# Verifies that shared/hooks/dispatch.sh routes the two Phase-2 commands
# (mantis-preference-update, mantis-judge) to their handlers instead of
# falling through to the `*)` unknown-command branch.
#
# Scenarios:
#   1. mantis-preference-update on a .py file -> exit 0, log "spawn mantis-preference-update"
#   2. mantis-judge on a .py file            -> exit 0, log "NOTE:mantis-judge-noop"
#   3. CLAUDE_SUBAGENT=1 guard               -> both return exit 0 without spawn/NOTE
#   4. Neither handler trips the "*) NOTE:unknown-command" fallthrough
#
# Usage: bash tests/hooks/test_phase2_dispatch.sh

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

DISPATCH="$REPO_ROOT/shared/hooks/dispatch.sh"
HOOKS_LOG="$REPO_ROOT/.claude/logs/hooks.log"
mkdir -p "$(dirname "$HOOKS_LOG")"
touch "$HOOKS_LOG"

fails=0
pass() { echo "[pass] $*"; }
fail() { echo "[FAIL] $*"; fails=$((fails+1)); }

_payload() {
    local file_path="$1"
    printf '{"tool_name":"Write","tool_input":{"file_path":"%s"}}' "$file_path"
}

_log_tail_after() {
    # Print only log lines added after $1 (an absolute byte offset).
    local before="$1"
    python - "$HOOKS_LOG" "$before" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
start = int(sys.argv[2])
if not p.exists():
    raise SystemExit
size = p.stat().st_size
with p.open("rb") as f:
    f.seek(start)
    data = f.read()
sys.stdout.buffer.write(data)
PY
}

_log_size() {
    if [[ -f "$HOOKS_LOG" ]]; then
        wc -c < "$HOOKS_LOG" | tr -d ' '
    else
        echo 0
    fi
}

# ---------------------------------------------------------------------------
# Scenario 1: mantis-preference-update routes to the preference handler
# ---------------------------------------------------------------------------
echo "[phase2] ===== scenario 1: mantis-preference-update ====="
before=$(_log_size)
set +e
_payload "tests/fixtures/quality-ladder/bad.py" | bash "$DISPATCH" mantis-preference-update
rc=$?
set -e
after=$(_log_size)
tail_bytes=$(_log_tail_after "$before")

if [[ $rc -eq 0 ]]; then
    pass "mantis-preference-update exit=0"
else
    fail "mantis-preference-update exit=$rc"
fi

if echo "$tail_bytes" | grep -q "spawn mantis-preference-update"; then
    pass "preference handler logged spawn"
elif echo "$tail_bytes" | grep -q "cmd=mantis-preference-update" && \
     echo "$tail_bytes" | grep -q "skip:"; then
    # Clean skip (e.g., no jq, no repo-root, missing script) is also acceptable
    # per the fail-open contract.
    pass "preference handler logged clean skip"
else
    fail "preference handler did not log spawn or clean skip"
    echo "  tail: $tail_bytes"
fi

if echo "$tail_bytes" | grep -q "NOTE:unknown-command"; then
    fail "preference handler fell through to *) — wiring missing"
fi

# Give the background spawn a beat to appear in the log so diagnostic output
# is complete — not a correctness requirement (async spawn runs regardless).
sleep 0.3 2>/dev/null || true

# ---------------------------------------------------------------------------
# Scenario 2: mantis-judge logs the honest no-op marker
# ---------------------------------------------------------------------------
echo
echo "[phase2] ===== scenario 2: mantis-judge (honest no-op) ====="
before=$(_log_size)
set +e
_payload "tests/fixtures/quality-ladder/bad.py" | bash "$DISPATCH" mantis-judge
rc=$?
set -e
after=$(_log_size)
tail_bytes=$(_log_tail_after "$before")

if [[ $rc -eq 0 ]]; then
    pass "mantis-judge exit=0"
else
    fail "mantis-judge exit=$rc"
fi

if echo "$tail_bytes" | grep -q "mantis-judge-noop"; then
    pass "judge handler emitted honest no-op marker"
else
    fail "judge handler did not log no-op marker"
    echo "  tail: $tail_bytes"
fi

if echo "$tail_bytes" | grep -q "NOTE:unknown-command"; then
    fail "judge handler fell through to *) — wiring missing"
fi

# ---------------------------------------------------------------------------
# Scenario 3: CLAUDE_SUBAGENT=1 guard short-circuits both
# ---------------------------------------------------------------------------
echo
echo "[phase2] ===== scenario 3: subagent guard ====="
for cmd in mantis-preference-update mantis-judge; do
    before=$(_log_size)
    set +e
    CLAUDE_SUBAGENT=1 _payload "tests/fixtures/quality-ladder/bad.py" | \
        CLAUDE_SUBAGENT=1 bash "$DISPATCH" "$cmd"
    rc=$?
    set -e
    after=$(_log_size)
    tail_bytes=$(_log_tail_after "$before")
    if [[ $rc -eq 0 ]]; then
        pass "$cmd under CLAUDE_SUBAGENT=1 exit=0"
    else
        fail "$cmd under CLAUDE_SUBAGENT=1 exit=$rc"
    fi
    # Guard MUST short-circuit before writing to the log for its own cmd.
    if echo "$tail_bytes" | grep -Eq "cmd=$cmd"; then
        fail "$cmd under CLAUDE_SUBAGENT=1 still dispatched (guard leaked)"
        echo "  tail: $tail_bytes"
    else
        pass "$cmd under CLAUDE_SUBAGENT=1 short-circuited (no dispatch log)"
    fi
done

echo
if (( fails == 0 )); then
    echo "[phase2] PASS  (0 failures)"
    exit 0
fi
echo "[phase2] FAIL  ($fails failure(s))"
exit 1
