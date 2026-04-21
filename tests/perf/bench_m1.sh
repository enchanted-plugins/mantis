#!/usr/bin/env bash
# M1 performance benchmark — 50-file sequential walker + dispatcher latency.
#
# Generates 50 synthetic .py files in /tmp/mantis-perf-<ts>/, each with a
# handful of M1-flaggable patterns (div-zero, index-oob, optional-deref).
# Then two measurement passes:
#
#   (1) M1 walker standalone: `python plugins/mantis-core/scripts/__main__.py
#       <file>` per file, sequential, wall-clock via `date +%s%N`.
#
#   (2) Dispatcher sync-path: `bash shared/hooks/dispatch.sh mantis-analyze`
#       with a stdin JSON payload — this measures only the synchronous
#       portion (the dispatcher backgrounds the real M1 work). The sync
#       budget in CLAUDE.md hooks.md is < 100ms on POSIX; Windows git-bash
#       typically pays an extra fork tax per invocation.
#
# Measurements: min / median / p95 / max, in milliseconds.
# Output: a table to stdout, and a JSON blob at tests/perf/bench_m1.latest.json
# for historical tracking (gitignored).
#
# Exit: always 0 — this is measurement, not a pass/fail gate.
#
# Usage: bash tests/perf/bench_m1.sh

set -uo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

DISPATCHER_PY="$REPO_ROOT/plugins/mantis-core/scripts/__main__.py"
DISPATCHER_SH="$REPO_ROOT/shared/hooks/dispatch.sh"
RESULTS_JSON="$REPO_ROOT/tests/perf/bench_m1.latest.json"

N_FILES=50
TS=$(date +%s 2>/dev/null || python -c 'import time; print(int(time.time()))')
WORKDIR="${TMPDIR:-/tmp}/mantis-perf-$TS"
mkdir -p "$WORKDIR"
echo "[bench-m1] workdir=$WORKDIR"

# Detect platform for honest reporting against the 100ms budget.
UNAME_S=$(uname -s 2>/dev/null || echo Unknown)
case "$UNAME_S" in
    MINGW*|MSYS*|CYGWIN*) PLATFORM="windows" ;;
    Darwin*)              PLATFORM="darwin" ;;
    Linux*)               PLATFORM="linux" ;;
    *)                    PLATFORM="unknown" ;;
esac
echo "[bench-m1] platform=$PLATFORM"

# Millisecond clock. `date +%s%N` works on GNU and git-bash; falls back to
# python on BSD/macOS where %N is unsupported.
_now_ms() {
    local ns
    ns=$(date +%s%N 2>/dev/null)
    if [[ "$ns" == *N || -z "$ns" ]]; then
        python -c 'import time; print(int(time.time()*1000))'
    else
        echo $(( ns / 1000000 ))
    fi
}

_cleanup() {
    rm -rf "$WORKDIR" 2>/dev/null || true
}
trap _cleanup EXIT

# -----------------------------------------------------------------------------
# Step 1 — generate 50 synthetic .py files, each ~20 LOC with a few
# M1-flaggable patterns. Enough variety to keep the AST walker honest but
# short enough that per-file runtime is dominated by Python startup, which
# is the realistic hot path.
# -----------------------------------------------------------------------------
echo "[bench-m1] generating $N_FILES synthetic files..."
python - "$WORKDIR" "$N_FILES" <<'PY'
import sys
from pathlib import Path

workdir = Path(sys.argv[1])
n = int(sys.argv[2])

TEMPLATE = '''"""Synthetic M1-target #{i}."""


def mean_{i}(nums):
    return sum(nums) / len(nums)  # PY-M1-001 candidate


def first_{i}(items):
    return items[0]  # PY-M1-002 candidate


def parse_{i}(s):
    parts = s.split(",")
    return int(parts[0])  # PY-M1-002 candidate on split result


def lookup_{i}(d, key):
    v = d.get(key)
    return v.upper()  # PY-M1-003 candidate (dict.get -> Optional)


def run_{i}():
    mean_{i}([])
    first_{i}([])
    parse_{i}("")
    lookup_{i}({{}}, "k")
'''

for i in range(n):
    (workdir / f"synth_{i:03d}.py").write_text(TEMPLATE.format(i=i), encoding="utf-8")
print(f"wrote {n} files to {workdir}")
PY

# Sort the file list so runs are reproducible.
mapfile -t FILES < <(ls "$WORKDIR"/synth_*.py | sort)
echo "[bench-m1] generated ${#FILES[@]} files"
echo

# -----------------------------------------------------------------------------
# Percentile helper — takes integers as argv, prints min/median/p95/max.
# Argv rather than stdin: on bash-on-Windows, a heredoc-fed `python -` cannot
# also consume piped stdin — the heredoc wins and stdin is empty. Passing
# values as argv sidesteps that cleanly and mirrors the pattern in
# tests/e2e/test_posttooluse_loop.sh § _p95.
# -----------------------------------------------------------------------------
_stats() {
    python -c "
import math, sys
vals = []
for tok in sys.argv[1:]:
    try:
        vals.append(int(tok))
    except ValueError:
        pass
if not vals:
    print('0 0 0 0')
    raise SystemExit
vals.sort()
n = len(vals)
mn = vals[0]; mx = vals[-1]
med = vals[n//2] if n%2==1 else (vals[n//2-1]+vals[n//2])//2
idx95 = max(0, math.ceil(0.95*n) - 1)
p95 = vals[idx95]
print(f'{mn} {med} {p95} {mx}')
" "$@"
}

# -----------------------------------------------------------------------------
# Step 2 — M1 walker standalone timings. One python invocation per file.
# -----------------------------------------------------------------------------
echo "[bench-m1] measuring M1 walker standalone..."
WALKER_MS=()
for f in "${FILES[@]}"; do
    t0=$(_now_ms)
    python "$DISPATCHER_PY" "$f" >/dev/null 2>&1 || true
    t1=$(_now_ms)
    WALKER_MS+=("$(( t1 - t0 ))")
done
echo "[bench-m1] walker sample count: ${#WALKER_MS[@]}"

# python on Windows emits CRLF; strip trailing CR from the last token so
# `%d` format specs don't trip on "438\r".
read -r W_MIN W_MED W_P95 W_MAX < <(_stats "${WALKER_MS[@]}")
W_MAX="${W_MAX%$'\r'}"

# -----------------------------------------------------------------------------
# Step 3 — Dispatcher sync-only timings. The dispatcher backgrounds the
# real M1 invocation and returns synchronously; we measure the parent-shell
# wall-clock. Payload: `{"tool_name":"Write","tool_input":{"file_path":...}}`.
# -----------------------------------------------------------------------------
echo "[bench-m1] measuring dispatcher sync path..."
DISP_MS=()
for f in "${FILES[@]}"; do
    payload=$(printf '{"tool_name":"Write","tool_input":{"file_path":"%s"}}' "$f")
    t0=$(_now_ms)
    printf '%s' "$payload" | bash "$DISPATCHER_SH" mantis-analyze >/dev/null 2>&1 || true
    t1=$(_now_ms)
    DISP_MS+=("$(( t1 - t0 ))")
done
echo "[bench-m1] dispatcher sample count: ${#DISP_MS[@]}"

read -r D_MIN D_MED D_P95 D_MAX < <(_stats "${DISP_MS[@]}")
D_MAX="${D_MAX%$'\r'}"

# Give the backgrounded M1 spawns a beat to drain before we tear down the
# workdir — otherwise we'd race their writes to /tmp/<workdir>/ on some
# hosts. The parent is already done measuring; this is courtesy cleanup.
sleep 1

# -----------------------------------------------------------------------------
# Step 4 — report.
# -----------------------------------------------------------------------------
BUDGET_MS=100

echo
echo "================== M1 benchmark (N=${N_FILES} files, $PLATFORM) =================="
printf '%-28s  min=%4dms  median=%4dms  p95=%4dms  max=%4dms\n' \
    "M1 walker (standalone):" "$W_MIN" "$W_MED" "$W_P95" "$W_MAX"
printf '%-28s  min=%4dms  median=%4dms  p95=%4dms  max=%4dms\n' \
    "Dispatcher (sync only):" "$D_MIN" "$D_MED" "$D_P95" "$D_MAX"
echo "----------------------------------------------------------------------------"
printf '%-28s  %s\n' "Sync budget (hooks.md):" "${BUDGET_MS}ms"
if (( D_P95 <= BUDGET_MS )); then
    printf '%-28s  OK (p95 %dms within budget)\n' "Dispatcher p95 vs budget:" "$D_P95"
else
    over=$(( D_P95 - BUDGET_MS ))
    printf '%-28s  OVER (p95 %dms, +%dms over budget; %s overhead likely)\n' \
        "Dispatcher p95 vs budget:" "$D_P95" "$over" "$PLATFORM"
fi
echo "============================================================================"
echo

# -----------------------------------------------------------------------------
# Step 5 — persist JSON for historical tracking.
# -----------------------------------------------------------------------------
mkdir -p "$(dirname "$RESULTS_JSON")"
python - "$RESULTS_JSON" "$PLATFORM" "$N_FILES" "$W_MIN" "$W_MED" "$W_P95" "$W_MAX" "$D_MIN" "$D_MED" "$D_P95" "$D_MAX" "$BUDGET_MS" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(out, platform, n, w_min, w_med, w_p95, w_max,
 d_min, d_med, d_p95, d_max, budget) = sys.argv[1:]

doc = {
    "ts": datetime.now(timezone.utc).isoformat(),
    "platform": platform,
    "n_files": int(n),
    "budget_ms": int(budget),
    "walker_ms": {"min": int(w_min), "median": int(w_med),
                  "p95": int(w_p95), "max": int(w_max)},
    "dispatcher_sync_ms": {"min": int(d_min), "median": int(d_med),
                           "p95": int(d_p95), "max": int(d_max)},
    "dispatcher_within_budget": int(d_p95) <= int(budget),
}
Path(out).write_text(json.dumps(doc, indent=2), encoding="utf-8")
print(f"wrote {out}")
PY

echo "[bench-m1] done"
exit 0
