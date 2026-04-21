#!/usr/bin/env bash
# Mantis M5 Sandbox Demo
#
# Walks the full M1 -> M5 -> verdict pipeline on an obviously-buggy fixture,
# prints human-readable output per stage, and ends with a summary. Idempotent:
# re-running produces identical output. Demo is informational, not a gate —
# always exits 0 regardless of verdict.

set -uo pipefail
REPO_ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# Color gating (NO_COLOR convention)
# ---------------------------------------------------------------------------
if [[ -n "${NO_COLOR:-}" ]] || ! [[ -t 1 ]]; then
    C_RESET="" C_BOLD="" C_DIM=""
    C_RED="" C_GREEN="" C_AMBER="" C_TEAL=""
    MARK_OK="[OK]"
    MARK_WARN="[WARN]"
    MARK_FAIL="[FAIL]"
    MARK_DOT="[-]"
    MARK_ARROW=">>"
else
    C_RESET="\033[0m" C_BOLD="\033[1m" C_DIM="\033[2m"
    C_RED="\033[31m" C_GREEN="\033[32m" C_AMBER="\033[33m" C_TEAL="\033[36m"
    MARK_OK="\u2713"
    MARK_WARN="\u26a0"
    MARK_FAIL="\u2717"
    MARK_DOT="\u2022"
    MARK_ARROW="\u2192"
fi

RULE="============================================================"

_stage() {
    printf '\n'
    printf "${C_BOLD}%s${C_RESET}\n" "===== STAGE $1: $2 ====="
}

_note() {
    printf "${C_DIM}%s${C_RESET}\n" "$1"
}

# ---------------------------------------------------------------------------
# Platform detection (cosmetic — sandbox.py makes the real call)
# ---------------------------------------------------------------------------
PLATFORM=$(uname -s 2>/dev/null || echo "Unknown")
if [[ "$PLATFORM" == *"MINGW"* ]] || [[ "$PLATFORM" == *"MSYS"* ]] || [[ "$PLATFORM" == *"CYGWIN"* ]]; then
    PLATFORM_LABEL="Windows (git-bash)"
else
    PLATFORM_LABEL="$PLATFORM"
fi

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
printf '\n'
printf "${C_BOLD}Mantis M5 Sandbox Demo${C_RESET}\n"
printf "%s\n" "$RULE"
printf "Pipeline: M1 static suspicion -> M5 sandbox confirm -> verdict\n"
printf "Host:     %s\n" "$PLATFORM_LABEL"
printf "Fixture:  tests/fixtures/quality-ladder/bad.py\n"
printf "Caps:     CPU=5s, AS=512MB, NOFILE=16, FSIZE=10MB, NPROC=0, alarm=10s\n"

# ---------------------------------------------------------------------------
# Reset state so the demo is idempotent
# ---------------------------------------------------------------------------
M1_LOG="plugins/mantis-core/state/review-flags.jsonl"
M5_LOG="plugins/mantis-sandbox/state/run-log.jsonl"
V_LOG="plugins/mantis-verdict/state/verdict.jsonl"
: > "$M1_LOG"
: > "$M5_LOG"
: > "$V_LOG"

FIXTURE="tests/fixtures/quality-ladder/bad.py"

# ---------------------------------------------------------------------------
# Stage 0: show the buggy code
# ---------------------------------------------------------------------------
_stage 0 "THE BUGGY CODE"
_note "Source under review ($FIXTURE):"
printf '\n'
nl -ba "$FIXTURE" | sed 's/^/    /'
printf '\n'
_note "Three bugs a human reviewer would flag:"
printf "  ${C_RED}${MARK_ARROW}${C_RESET} L2  div-zero         sum(nums) / len(nums)       when nums=[]\n"
printf "  ${C_RED}${MARK_ARROW}${C_RESET} L6  index-oob        [u for u ...][0]            when nobody matches\n"
printf "  ${C_RED}${MARK_ARROW}${C_RESET} L14 mutable-default  def tally(items, seen=[]):  state leaks across calls\n"

# ---------------------------------------------------------------------------
# Stage 1: M1 static pass
# ---------------------------------------------------------------------------
_stage 1 "M1 STATIC SUSPICION (Cousot interval + nullability walk)"
_note "Invoking: python plugins/mantis-core/scripts/__main__.py $FIXTURE"
M1_OUT=$(python plugins/mantis-core/scripts/__main__.py "$FIXTURE" 2>&1 | tail -2 | head -1)
printf '\n%s\n\n' "$M1_OUT"
_note "M1 flags (review-flags.jsonl):"
python - <<'PY'
import json, pathlib
rows = []
for ln in pathlib.Path('plugins/mantis-core/state/review-flags.jsonl').read_text(encoding='utf-8').splitlines():
    if not ln.strip():
        continue
    r = json.loads(ln)
    rows.append((r['severity'], r['file'], r['line'], r['rule_id'], r['flag_class']))
for sev, f, line, rid, fc in rows:
    print(f"  [{sev}] {f}:{line}  {rid}  {fc}")
PY

# ---------------------------------------------------------------------------
# Stage 2: M5 sandbox
# ---------------------------------------------------------------------------
_stage 2 "M5 SANDBOX CONFIRMATION (bounded subprocess dry-run)"
_note "Invoking: python plugins/mantis-sandbox/scripts/sandbox.py"
printf '\n'
M5_OUT=$(python plugins/mantis-sandbox/scripts/sandbox.py 2>&1 | tail -1)
printf '%s\n\n' "$M5_OUT"

# Render each run-log record with status markers
python - <<'PY'
import json, pathlib, os

# Reconstruct color env — child processes don't inherit bash vars reliably
no_color = bool(os.environ.get('NO_COLOR'))

def paint(s, code):
    if no_color:
        return s
    return f"\033[{code}m{s}\033[0m"

TEAL, AMBER, RED, DIM = '36', '33', '31', '2'

marker = {
    'confirmed-bug': ('\u2717 CONFIRMED', RED),
    'timeout-without-confirmation': ('\u26a0 TIMEOUT', AMBER),
    'no-bug-found': ('\u2713 CLEARED', TEAL),
    'platform-unsupported': ('\u26a0 SKIPPED (platform)', AMBER),
    'input-synthesis-failed': ('\u2022 NO WITNESS', DIM),
    'sandbox-error': ('\u2717 INFRA ERROR', RED),
}

if no_color:
    marker = {k: (v[0].replace('\u2717','[FAIL]').replace('\u26a0','[WARN]').replace('\u2713','[OK]').replace('\u2022','[-]'), v[1]) for k,v in marker.items()}

rows = [json.loads(l) for l in pathlib.Path('plugins/mantis-sandbox/state/run-log.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
for r in rows:
    flag = r.get('flag_ref') or {}
    status = r.get('status', 'unknown')
    backend = r.get('backend', '?')
    mark, color = marker.get(status, (status, DIM))
    witness = r.get('witness')
    w_str = 'no witness' if not witness else (witness.get('reason') or 'witness provided')
    rid = flag.get('rule_id', '?')
    line = flag.get('line', '?')
    print(f"  {paint(mark, color)}  L{line} {rid}  backend={backend}  ({w_str})")
PY

printf '\n'
# Platform-specific explainer
if echo "$M5_OUT" | grep -q "platform-unsupported"; then
    printf "${C_AMBER}Host is %s; no resource.setrlimit available.${C_RESET}\n" "$PLATFORM_LABEL"
    printf "M5 skipped honestly per contract \u00a72 (never silently pretends it ran).\n"
    printf "On Linux/macOS/WSL, each flag above would attempt a witness execution.\n"
fi

# ---------------------------------------------------------------------------
# Stage 3: verdict compose
# ---------------------------------------------------------------------------
_stage 3 "VERDICT COMPOSE (M1 + M5 + M6 + M7 -> DEPLOY/HOLD/FAIL)"
_note "Invoking: python plugins/mantis-verdict/scripts/compose.py --file $FIXTURE"
printf '\n'
V_OUT=$(python plugins/mantis-verdict/scripts/compose.py --file "$FIXTURE" 2>&1 | tail -1)
printf '%s\n\n' "$V_OUT"

python - <<'PY'
import json, pathlib, os
no_color = bool(os.environ.get('NO_COLOR'))
def paint(s, code):
    return s if no_color else f"\033[{code}m{s}\033[0m"

v = json.loads(pathlib.Path('plugins/mantis-verdict/state/verdict.jsonl').read_text(encoding='utf-8').splitlines()[-1])
verdict = v['verdict']
conf = v['confidence']
verdict_color = {'DEPLOY': '32', 'HOLD': '33', 'FAIL': '31'}.get(verdict, '0')

print("  Per-engine demand:")
for e in v['engines']:
    status = e['status']
    dem = e['demands']
    dem_color = {'DEPLOY': '32', 'HOLD': '33', 'FAIL': '31'}.get(dem, '0')
    print(f"    {e['engine']}:  status={status:<15} demands={paint(dem, dem_color)}")

print()
for r in v.get('reasons', [])[:6]:
    print(f"    {r}")
PY

# ---------------------------------------------------------------------------
# Stage 4: summary
# ---------------------------------------------------------------------------
_stage 4 "SUMMARY"

FINAL_VERDICT=$(python -c "import json,pathlib; v=json.loads(pathlib.Path('plugins/mantis-verdict/state/verdict.jsonl').read_text(encoding='utf-8').splitlines()[-1]); print(v['verdict'], v['confidence'])")
VERDICT_LEVEL=${FINAL_VERDICT% *}
VERDICT_CONF=${FINAL_VERDICT#* }

printf '\n'
case "$VERDICT_LEVEL" in
    DEPLOY) printf "${C_GREEN}${C_BOLD}VERDICT: DEPLOY (confidence: %s)${C_RESET}\n" "$VERDICT_CONF" ;;
    HOLD)   printf "${C_AMBER}${C_BOLD}VERDICT: HOLD (confidence: %s)${C_RESET}\n" "$VERDICT_CONF" ;;
    FAIL)   printf "${C_RED}${C_BOLD}VERDICT: FAIL (confidence: %s)${C_RESET}\n" "$VERDICT_CONF" ;;
    *)      printf "${C_BOLD}VERDICT: %s (confidence: %s)${C_RESET}\n" "$VERDICT_LEVEL" "$VERDICT_CONF" ;;
esac

printf '\n'
if echo "$M5_OUT" | grep -q "platform-unsupported"; then
    printf "%s\n" "Interpretation: M1 alone is sufficient to issue FAIL (>= 3 HIGH flags trip the"
    printf "%s\n" "verdict bar's hard-fail threshold). M5 would confirm each bug with a concrete"
    printf "%s\n" "witness input on Linux/macOS/WSL, promoting the confidence from 'reduced' to"
    printf "%s\n" "'high'. Today's host can't run the sandbox; the pipeline degrades honestly."
else
    printf "%s\n" "Interpretation: M1 flagged the runtime-failure candidates; M5 attempted"
    printf "%s\n" "witness execution in a bounded subprocess (caps per CLAUDE.md \u00a72). See the"
    printf "%s\n" "run-log records above for each confirmation or clearing."
fi

printf '\n'
printf "%s\n" "Want to run this in CI? See plugins/mantis-sandbox/README.md \u00a7 Running it."
printf "%s\n" "Full contract: CLAUDE.md \u00a7 Verdict bar, Engine table, Behavioral contract 2."
printf '\n'

exit 0
