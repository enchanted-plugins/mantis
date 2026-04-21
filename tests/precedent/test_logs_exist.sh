#!/usr/bin/env bash
# tests/precedent/test_logs_exist.sh — verify each of the 5 engine plugins
# ships a seed precedent-log.md with the shared/conduct/precedent.md format.
#
# Canonical headers per shared/conduct/precedent.md example:
#   **Command that failed:** / **Why it failed:** / **What worked:**
#   **Signal:** / **Tags:**
# We grep for the common substring prefix (`**Command`, `**Why`, ...) so both
# canonical and minor variants match.
#
# Harness style: mirror tests/regression/, tests/verdict/ — no pytest, no deps.

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

PLUGINS=(
    mantis-core
    mantis-sandbox
    mantis-preference
    mantis-rubric
    mantis-verdict
)

REQUIRED_HEADERS=(
    '**Command'
    '**Why'
    '**What worked'
    '**Signal'
    '**Tags'
)

fails=0
pass() { echo "[pass] $*"; }
fail() { echo "[FAIL] $*"; fails=$((fails+1)); }

for p in "${PLUGINS[@]}"; do
    log="$REPO_ROOT/plugins/$p/state/precedent-log.md"
    if [[ ! -f "$log" ]]; then
        fail "$p: precedent-log.md missing at $log"
        continue
    fi
    pass "$p: log exists"

    missing=()
    for hdr in "${REQUIRED_HEADERS[@]}"; do
        # fixed-string grep; avoid regex pitfalls on the ** markdown markers
        if ! grep -qF -- "$hdr" "$log"; then
            missing+=("$hdr")
        fi
    done
    if (( ${#missing[@]} == 0 )); then
        pass "$p: all 5 required headers present"
    else
        fail "$p: missing header(s): ${missing[*]}"
    fi
done

echo ""
if (( fails == 0 )); then
    echo "[ok] precedent logs: ${#PLUGINS[@]}/${#PLUGINS[@]} plugins clean"
    exit 0
else
    echo "[FAIL] $fails precedent-log assertion(s) failed"
    exit 1
fi
