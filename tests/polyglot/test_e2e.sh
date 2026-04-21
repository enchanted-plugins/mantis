#!/usr/bin/env bash
# Polyglot E2E — dispatcher routing test across all supported languages.
#
# For each fixture under tests/fixtures/polyglot/, invoke the M1 dispatcher
# and assert that the `substrate` key it emits to stderr matches the expected
# routing for that extension. This verifies *routing*, not *detection* —
# the upstream linters (staticcheck, clippy, spotbugs, clang-tidy, rubocop,
# shellcheck, semgrep) are not required on this host. When a linter is
# absent, the adapter's analyze() returns [] cleanly and the dispatcher
# still emits the expected substrate tag.
#
# Control case (sample.md): asserts the dispatcher does not mutate the M1
# state log. Current wiring routes .md to the polyglot semgrep adapter,
# which returns [] when semgrep is absent — so state is untouched either
# way. If a Markdown adapter ships in Phase 2, this assertion still holds
# because control-case prose has nothing flaggable.
#
# Usage: bash tests/polyglot/test_e2e.sh
# Exit: 0 on all-pass, 1 on any routing mismatch or unexpected state growth.

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

FIXTURE_DIR="$REPO_ROOT/tests/fixtures/polyglot"
M1_LOG="$REPO_ROOT/plugins/mantis-core/state/review-flags.jsonl"
DISPATCHER="$REPO_ROOT/plugins/mantis-core/scripts/__main__.py"

# Expected substrate tag per fixture. Trailing-space-separated so bash `case`
# can match. `sample.py` varies on ruff presence — we accept either.
_expected_for() {
    case "$1" in
        sample.py)   echo "ast-only|ruff+ast" ;;
        sample.ts)   echo "adapter:semgrep" ;;
        sample.js)   echo "adapter:semgrep" ;;
        sample.go)   echo "adapter:go,semgrep" ;;
        sample.rs)   echo "adapter:rust,semgrep" ;;
        sample.java) echo "adapter:java,semgrep" ;;
        sample.cpp)  echo "adapter:cpp,semgrep" ;;
        sample.rb)   echo "adapter:ruby,semgrep" ;;
        sample.sh)   echo "adapter:shell,semgrep" ;;
        sample.yml)  echo "adapter:semgrep" ;;
        sample.md)   echo "adapter:semgrep" ;;
        *)           echo "UNKNOWN" ;;
    esac
}

# sample.md is the control case: state must not grow (lines_written == 0
# AND log-file line count unchanged).
_is_control_case() {
    [[ "$1" == "sample.md" ]]
}

fails=0
pass() { echo "[pass] $*"; }
fail() { echo "[FAIL] $*"; fails=$((fails+1)); }
note() { echo "[NOTE] $*"; }

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

# Extract the "substrate" key from the final JSON line on stderr. The
# dispatcher emits two JSON lines for .py (substrate-breakdown then
# summary); both have a substrate key, so the summary line wins. For
# non-.py it emits just the summary line.
_extract_substrate() {
    python - "$1" <<'PY'
import json
import sys
from pathlib import Path
raw = Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace")
substrate = None
lines_written = None
total_flags = None
for raw_line in raw.splitlines():
    line = raw_line.strip()
    if not line or not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        continue
    if "substrate" in obj:
        substrate = obj.get("substrate")
    if "lines_written" in obj:
        lines_written = obj.get("lines_written")
    if "total" in obj:
        total_flags = obj.get("total")
print(f"{substrate}\t{lines_written}\t{total_flags}")
PY
}

# -----------------------------------------------------------------------------
# Setup — clear the M1 log so routing counts are clean, and snapshot baseline.
# -----------------------------------------------------------------------------
mkdir -p "$(dirname "$M1_LOG")"
: > "$M1_LOG"   # truncate
baseline=$(_count_lines "$M1_LOG")
echo "[polyglot-e2e] M1 log truncated; baseline=$baseline"
echo

# -----------------------------------------------------------------------------
# Routing summary accumulator
# -----------------------------------------------------------------------------
declare -a SUMMARY_ROWS=()

# -----------------------------------------------------------------------------
# Run each fixture sequentially
# -----------------------------------------------------------------------------
FIXTURES=(sample.py sample.ts sample.js sample.go sample.rs sample.java sample.cpp sample.rb sample.sh sample.yml sample.md)

for fix in "${FIXTURES[@]}"; do
    path="$FIXTURE_DIR/$fix"
    if [[ ! -f "$path" ]]; then
        fail "$fix: fixture missing at $path"
        SUMMARY_ROWS+=("$(printf '%-13s | %-22s | %-5s | %s' "$fix" "MISSING" "-" "fail")")
        continue
    fi

    lines_before=$(_count_lines "$M1_LOG")
    stderr_tmp="$(mktemp 2>/dev/null || echo "/tmp/polyglot.$$.$fix.err")"

    # Invoke the dispatcher. Stdout is the summary JSON (same as stderr on
    # success); we read from stderr to match the dispatcher's contract.
    set +e
    python "$DISPATCHER" "$path" 1>/dev/null 2>"$stderr_tmp"
    rc=$?
    set -e

    if [[ $rc -ne 0 ]]; then
        fail "$fix: dispatcher exit=$rc (expected 0)"
    fi

    IFS=$'\t' read -r substrate lines_written total_flags < <(_extract_substrate "$stderr_tmp")

    lines_after=$(_count_lines "$M1_LOG")
    state_delta=$(( lines_after - lines_before ))

    rm -f "$stderr_tmp" 2>/dev/null || true

    # -------------------------------------------------------------------------
    # Routing assertion: substrate matches the expected pattern for this ext.
    # -------------------------------------------------------------------------
    expected=$(_expected_for "$fix")
    matched=0
    # Split on | so sample.py accepts either ast-only or ruff+ast.
    IFS='|' read -ra alts <<< "$expected"
    for alt in "${alts[@]}"; do
        if [[ "$substrate" == "$alt" ]]; then
            matched=1
            break
        fi
    done

    status="fail"
    if (( matched == 1 )); then
        pass "$fix: substrate=$substrate (expected=$expected)"
        status="ok"
    else
        fail "$fix: substrate=$substrate (expected=$expected)"
    fi

    # -------------------------------------------------------------------------
    # Control case — sample.md must not mutate state.
    # -------------------------------------------------------------------------
    if _is_control_case "$fix"; then
        if [[ "$lines_written" == "0" && $state_delta -eq 0 ]]; then
            pass "$fix: state not mutated (lines_written=0, log delta=0)"
            status="skip"
        else
            fail "$fix: control case mutated state (lines_written=$lines_written, delta=$state_delta)"
            status="fail"
        fi
        # Honest surfacing: the dispatcher currently routes .md to semgrep
        # rather than hard-skipping. It produces 0 flags only because the
        # semgrep binary is absent. This is a routing observation, not a
        # harness bug — we flag it once per run.
        note "$fix: routed to $substrate (not hard-skipped). Control holds because semgrep returns [] when absent. If semgrep ever ships on-host with rules that match .md prose, this assertion will need a stronger gate."
    fi

    SUMMARY_ROWS+=("$(printf '%-13s | %-22s | %-5s | %s' "$fix" "$substrate" "${total_flags:-?}" "$status")")
done

# -----------------------------------------------------------------------------
# Routing summary table
# -----------------------------------------------------------------------------
echo
echo "===================== polyglot routing summary ====================="
printf '%-13s | %-22s | %-5s | %s\n' "fixture" "substrate" "flags" "status"
echo "--------------+------------------------+-------+-------"
for row in "${SUMMARY_ROWS[@]}"; do
    echo "$row"
done
echo "===================================================================="
echo

final=$(_count_lines "$M1_LOG")
echo "[polyglot-e2e] final M1 log lines: $final"

if (( fails == 0 )); then
    echo "[polyglot-e2e] PASS (11 fixtures, 0 failures)"
    exit 0
fi
echo "[polyglot-e2e] FAIL ($fails failure(s) across 11 fixtures)"
exit 1
