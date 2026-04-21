#!/usr/bin/env bash
# Regression coverage for three e2e bugs fixed on 2026-04-21:
#
#   bug-1: shared/hooks/dispatch.sh (mantis-sandbox branch) passed the source
#          file path as sandbox.py argv[1]; sandbox.py's argv[1] is the
#          review-flags.jsonl input path, so M5 silently no-op'd on hook fire.
#
#   bug-2: shared/hooks/dispatch.sh (mantis-verdict-compose branch) lacked a
#          _is_python_file gate; Write/Edit on .md/.json/.txt composed a
#          preliminary DEPLOY record. Stop-event invocations (empty
#          FILE_PATH) must STILL dispatch.
#
#   bug-3: plugins/mantis-preference/scripts/override.py used parents[3] for
#          repo root; from plugins/mantis-preference/scripts/ that overshoots
#          to enchanted-skills/ rather than enchanted-skills/mantis/. The
#          default overrides.json path landed outside the repo.
#
# Harness conventions (mirror tests/e2e/, tests/sandbox/, tests/verdict/):
#   - bash, no python runner; python called inline only for assertion probes
#   - backs up state files before run, restores on exit (trap)
#   - prints [pass] bug-N: ... per assertion
#   - exits 1 on any failure, 0 otherwise

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

SB_DISPATCH="$REPO_ROOT/plugins/mantis-sandbox/hooks/dispatch.sh"
V_DISPATCH="$REPO_ROOT/plugins/mantis-verdict/hooks/dispatch.sh"
M1_LOG="$REPO_ROOT/plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="$REPO_ROOT/plugins/mantis-sandbox/state/run-log.jsonl"
V_LOG="$REPO_ROOT/plugins/mantis-verdict/state/verdict.jsonl"
OVR_PATH="$REPO_ROOT/plugins/mantis-preference/state/overrides.json"
OVERRIDE_PY="$REPO_ROOT/plugins/mantis-preference/scripts/override.py"
BAD_PY="$REPO_ROOT/tests/fixtures/quality-ladder/bad.py"

fails=0
pass() { echo "[pass] $*"; }
fail() { echo "[FAIL] $*"; fails=$((fails+1)); }

# ---------------------------------------------------------------------------
# Back up state, restore on exit. We mutate M1/M5/verdict logs and
# overrides.json — every mutation is reversed.
# ---------------------------------------------------------------------------
BACKUP_DIR=$(mktemp -d 2>/dev/null || echo "$TMPDIR/mantis-regression-$$")
mkdir -p "$BACKUP_DIR"

_backup() {
    local src="$1" dst="$BACKUP_DIR/$(basename "$1")"
    if [[ -f "$src" ]]; then
        cp "$src" "$dst"
    else
        # Record absence so restore removes a file we created.
        : > "$BACKUP_DIR/$(basename "$1").absent"
    fi
}

_restore() {
    local src="$1" dst="$BACKUP_DIR/$(basename "$1")"
    if [[ -f "$dst.absent" ]]; then
        rm -f "$src"
    elif [[ -f "$dst" ]]; then
        cp "$dst" "$src"
    fi
}

cleanup() {
    _restore "$M1_LOG"
    _restore "$M5_LOG"
    _restore "$V_LOG"
    _restore "$OVR_PATH"
    rm -rf "$BACKUP_DIR"
}
trap cleanup EXIT

_backup "$M1_LOG"
_backup "$M5_LOG"
_backup "$V_LOG"
_backup "$OVR_PATH"

_count_lines() {
    python - "$1" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
if not p.exists():
    print(0); raise SystemExit
n = 0
with p.open("rb") as f:
    for line in f:
        if line.strip():
            n += 1
print(n)
PY
}

_wait_for_growth() {
    # poll path until line-count > baseline, or timeout (seconds).
    local path="$1" baseline="$2" timeout="$3"
    local deadline=$(( $(date +%s) + timeout ))
    while (( $(date +%s) < deadline )); do
        local cur
        cur=$(_count_lines "$path")
        if (( cur > baseline )); then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

_simulate_payload() {
    local file_path="$1"
    printf '{"tool_name":"Write","tool_input":{"file_path":"%s"}}' "$file_path"
}

# =============================================================================
# Bug 1 — sandbox dispatcher no longer passes source path as argv[0]; M5
# writes a record per flag consumed from review-flags.jsonl.
# =============================================================================
echo "[bug-1] seeding M1 flags for bad.py, then firing sandbox dispatcher"

# Seed M1 flags via the real walker (tests/e2e does the same). Falls back
# gracefully — sandbox needs only ONE flag to prove argv wiring is fixed.
mkdir -p "$(dirname "$M1_LOG")"
: > "$M1_LOG"
python "$REPO_ROOT/plugins/mantis-core/scripts/__main__.py" "$BAD_PY" \
    >/dev/null 2>&1 || true

m1_seeded=$(_count_lines "$M1_LOG")
if (( m1_seeded == 0 )); then
    fail "bug-1: precondition — M1 walker produced 0 flags for bad.py"
else
    echo "[bug-1] seeded $m1_seeded M1 flag(s)"
fi

# Truncate M5 log so delta is unambiguous.
: > "$M5_LOG"
m5_before=$(_count_lines "$M5_LOG")

# Fire via the exact per-plugin wrapper Claude Code invokes.
payload=$(_simulate_payload "$BAD_PY")
printf '%s' "$payload" | bash "$SB_DISPATCH" mantis-sandbox
rc_sb=$?
if (( rc_sb != 0 )); then
    fail "bug-1: sandbox dispatch exit=$rc_sb"
fi

# Poll for M5 growth (background spawn — give it 30s ceiling).
if _wait_for_growth "$M5_LOG" "$m5_before" 30; then
    m5_after=$(_count_lines "$M5_LOG")
    m5_delta=$(( m5_after - m5_before ))
    if (( m5_delta >= 1 )); then
        pass "bug-1: M5 run-log gained $m5_delta record(s) — sandbox argv wiring fixed"
    else
        fail "bug-1: M5 run-log delta=$m5_delta (expected >=1)"
    fi
else
    fail "bug-1: M5 run-log did not grow within 30s (sandbox silently no-op'd)"
fi

# =============================================================================
# Bug 2 — verdict-compose dispatcher gates on non-.py FILE_PATH; Stop-event
# (empty FILE_PATH) still dispatches.
# =============================================================================
echo "[bug-2] firing verdict dispatcher with .md payload (should skip)"

: > "$V_LOG"
v_before=$(_count_lines "$V_LOG")

md_payload=$(_simulate_payload "$REPO_ROOT/README.md")
printf '%s' "$md_payload" | bash "$V_DISPATCH" mantis-verdict-compose
rc_v1=$?
if (( rc_v1 != 0 )); then
    fail "bug-2: verdict dispatch (md payload) exit=$rc_v1"
fi

# Give background process a generous window to prove it DID NOT spawn.
sleep 2.0
v_after_md=$(_count_lines "$V_LOG")
if (( v_after_md == v_before )); then
    pass "bug-2: verdict dispatcher skipped non-.py FILE_PATH (no record written)"
else
    fail "bug-2: verdict dispatcher wrote $((v_after_md - v_before)) record(s) on .md file"
fi

echo "[bug-2] firing verdict dispatcher with empty FILE_PATH (Stop event; should run)"
# Stop-event payload: no tool_input.file_path. We use a minimal payload
# with empty file_path — the dispatcher parses `""` and must still spawn.
empty_payload='{"tool_name":"","tool_input":{"file_path":""}}'
v_before_stop=$(_count_lines "$V_LOG")
printf '%s' "$empty_payload" | bash "$V_DISPATCH" mantis-verdict-compose
rc_v2=$?
if (( rc_v2 != 0 )); then
    fail "bug-2: verdict dispatch (empty payload) exit=$rc_v2"
fi

if _wait_for_growth "$V_LOG" "$v_before_stop" 30; then
    v_after_stop=$(_count_lines "$V_LOG")
    v_delta_stop=$(( v_after_stop - v_before_stop ))
    pass "bug-2: verdict dispatcher composed $v_delta_stop record(s) on empty FILE_PATH (Stop event)"
else
    fail "bug-2: verdict dispatcher did not grow log on empty FILE_PATH within 30s"
fi

# =============================================================================
# Bug 3 — override.py writes under plugins/mantis-preference/state/
# (parents[2] idiom), not outside the repo (parents[3]).
# =============================================================================
echo "[bug-3] invoking override.py disable; asserting written path is inside repo"

# Clear existing overrides to make the write visible.
rm -f "$OVR_PATH"

python "$OVERRIDE_PY" --dev alice --rule PY-M1-001 disable >/dev/null 2>&1
rc_ovr=$?
if (( rc_ovr != 0 )); then
    fail "bug-3: override.py exit=$rc_ovr"
fi

# Canonical location must exist after the disable.
if [[ -f "$OVR_PATH" ]]; then
    # Assert the alice entry landed in this file (not some sibling path).
    hit=$(python - "$OVR_PATH" <<'PY'
import json, sys
try:
    data = json.loads(open(sys.argv[1], encoding="utf-8").read())
except Exception:
    print("0"); raise SystemExit
if any(e.get("dev_id") == "alice" and e.get("rule_id") == "PY-M1-001"
       for e in data):
    print("1")
else:
    print("0")
PY
)
    if [[ "$hit" == "1" ]]; then
        pass "bug-3: override.py wrote to $OVR_PATH (inside repo, parents[2] idiom)"
    else
        fail "bug-3: overrides.json exists at canonical path but lacks alice/PY-M1-001"
    fi
else
    fail "bug-3: override.py did not write to canonical path $OVR_PATH"
fi

# Also explicitly prove no file was written at the overshoot location.
OVERSHOOT="$REPO_ROOT/../plugins/mantis-preference/state/overrides.json"
if [[ -f "$OVERSHOOT" ]]; then
    fail "bug-3: override.py wrote to overshoot path $OVERSHOOT (parents[3] regression)"
else
    pass "bug-3: no write at parents[3] overshoot path"
fi

# =============================================================================
echo
if (( fails == 0 )); then
    echo "[regression] PASS  (3 bugs, all assertions cleared)"
    exit 0
fi
echo "[regression] FAIL  ($fails assertion failure(s))"
exit 1
