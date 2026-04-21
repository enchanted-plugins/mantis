#!/usr/bin/env bash
# tests/learnings/test_aggregation.sh
#
# Round-trip harness for Gauss Accumulation export.
#
# Stages:
#   1. Clear every plugin's learnings.jsonl (leaving other state untouched).
#   2. Seed 5 synthetic entries per plugin by direct JSONL file write.
#   3. Run `python shared/learnings.py export`.
#   4. Assert shared/learnings.json has 25 entries with correct `plugin` tags.
#   5. Re-run export; assert no duplicates added.
#
# Advisory: on failure prints the diff; exits non-zero. No side effects on
# plugin state beyond learnings.jsonl.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${HERE}/../.." && pwd)"
cd "${REPO_ROOT}"

PYTHON="${PYTHON:-python}"
PLUGINS=(mantis-core mantis-sandbox mantis-verdict mantis-preference mantis-rubric)
AGG="${REPO_ROOT}/shared/learnings.json"

echo "[harness] repo:   ${REPO_ROOT}"
echo "[harness] python: $(${PYTHON} --version 2>&1)"

# Stage 1: clear per-plugin learnings.jsonl.
for p in "${PLUGINS[@]}"; do
    log="${REPO_ROOT}/plugins/${p}/state/learnings.jsonl"
    mkdir -p "$(dirname "${log}")"
    : > "${log}"
done
rm -f "${AGG}"

# Stage 2: seed 5 entries per plugin (25 total). Use distinct ts so
# dedup leaves them all alone on the first pass.
REPO_ROOT="${REPO_ROOT}" ${PYTHON} - <<'PY'
import json, os
from pathlib import Path
from datetime import datetime, timedelta, timezone

repo = Path(os.environ["REPO_ROOT"])
plugins = ["mantis-core", "mantis-sandbox", "mantis-verdict",
           "mantis-preference", "mantis-rubric"]
codes = ["F01", "F02", "F05", "F06", "F11", "F13", "F14"]
base = datetime(2026, 4, 20, 12, 0, 0, tzinfo=timezone.utc)

for pi, plugin in enumerate(plugins):
    path = repo / "plugins" / plugin / "state" / "learnings.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(5):
            ts = (base + timedelta(minutes=pi * 10 + i)).isoformat()
            rec = {
                "plugin": plugin,
                "code": codes[(pi + i) % len(codes)],
                "hypothesis": f"seed-{plugin}-{i}",
                "outcome": f"outcome-{i}",
                "counter": "test-counter",
                "axis": f"axis-{i}",
                "ts": ts,
            }
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
PY

# Stage 3: export.
REPO_ROOT="${REPO_ROOT}" ${PYTHON} shared/learnings.py export

# Stage 4: assert 25 entries and per-plugin counts.
${PYTHON} - "${AGG}" <<'PY'
import json, sys
from collections import Counter

path = sys.argv[1]
with open(path, "r", encoding="utf-8") as fh:
    snap = json.load(fh)

assert "generated_at" in snap, f"missing generated_at: {list(snap.keys())}"
entries = snap["entries"]
assert len(entries) == 25, f"expected 25 entries, got {len(entries)}"

plugin_counts = Counter(e["plugin"] for e in entries)
expected_plugins = {"mantis-core", "mantis-sandbox", "mantis-verdict",
                    "mantis-preference", "mantis-rubric"}
assert set(plugin_counts) == expected_plugins, \
    f"plugin set mismatch: {set(plugin_counts)} vs {expected_plugins}"
for p in expected_plugins:
    assert plugin_counts[p] == 5, \
        f"plugin {p} had {plugin_counts[p]} entries, expected 5"

# Every entry carries a valid code.
for e in entries:
    assert e["code"] in {f"F{n:02d}" for n in range(1, 15)}, \
        f"bad code in entry: {e}"

print(json.dumps({"entries": len(entries), "per_plugin": dict(plugin_counts)}))
PY

# Stage 5: re-run export; count must stay at 25 (dedup).
REPO_ROOT="${REPO_ROOT}" ${PYTHON} shared/learnings.py export >/dev/null

${PYTHON} - "${AGG}" <<'PY'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    snap = json.load(fh)
n = len(snap["entries"])
assert n == 25, f"dedup failed: expected 25 after re-export, got {n}"
print(json.dumps({"post_reexport_entries": n}))
PY

echo "[harness] PASS"
exit 0
