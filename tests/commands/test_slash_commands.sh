#!/usr/bin/env bash
# Verify the three Mantis slash-command markdown files and the /mantis-disable
# end-to-end invocation of override.py.
#
# Scope fence: we do NOT simulate Claude Code's slash dispatch — that belongs to
# the client. We verify (1) the command markdown files exist with well-formed
# frontmatter and (2) the one command with Python glue (/mantis-disable)
# actually produces the expected state mutation.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

red() { printf "\033[31m%s\033[0m" "$*"; }
grn() { printf "\033[32m%s\033[0m" "$*"; }

fail=0
pass=0

check() {
  local name="$1"; shift
  if "$@"; then
    printf "  %s %s\n" "$(grn '✓')" "$name"
    pass=$((pass + 1))
  else
    printf "  %s %s\n" "$(red '✗')" "$name"
    fail=$((fail + 1))
  fi
}

# ---- 1. The three command files exist ---------------------------------------

CMD_REVIEW="plugins/mantis-core/commands/mantis-review.md"
CMD_EXPLAIN="plugins/mantis-rubric/commands/mantis-explain.md"
CMD_DISABLE="plugins/mantis-preference/commands/mantis-disable.md"

check "mantis-review.md exists"  test -f "$CMD_REVIEW"
check "mantis-explain.md exists" test -f "$CMD_EXPLAIN"
check "mantis-disable.md exists" test -f "$CMD_DISABLE"

# ---- 2. Frontmatter parses (YAML between two --- fences) --------------------

frontmatter_ok() {
  local path="$1"
  python - "$path" <<'PY'
import sys, re, pathlib
p = pathlib.Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
# Expect: opening '---\n' at pos 0, a closing '\n---\n' later.
m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
if not m:
    print(f"no frontmatter fences in {p}", file=sys.stderr)
    sys.exit(1)
body = m.group(1)
# Minimal YAML sanity: every top-level key line is "word: ..." or list item.
# We parse with PyYAML if available, else a shape check.
try:
    import yaml
    data = yaml.safe_load(body)
    if not isinstance(data, dict):
        raise ValueError("frontmatter is not a mapping")
    if "description" not in data:
        raise ValueError("description key missing")
except ImportError:
    # stdlib-only fallback: look for 'description:' key literal
    if "description" not in body:
        print(f"no 'description' key in {p}", file=sys.stderr)
        sys.exit(1)
except Exception as e:  # yaml.YAMLError, ValueError
    print(f"frontmatter error in {p}: {e}", file=sys.stderr)
    sys.exit(1)
PY
}

check "mantis-review  frontmatter parses"  frontmatter_ok "$CMD_REVIEW"
check "mantis-explain frontmatter parses"  frontmatter_ok "$CMD_EXPLAIN"
check "mantis-disable frontmatter parses"  frontmatter_ok "$CMD_DISABLE"

# Per the user spec, also confirm exactly 2 '---' fence lines per file.
fence_count_ok() {
  local path="$1"
  local count
  count="$(grep -c '^---$' "$path" || true)"
  [[ "$count" == "2" ]]
}
check "mantis-review  has 2 frontmatter fences"  fence_count_ok "$CMD_REVIEW"
check "mantis-explain has 2 frontmatter fences"  fence_count_ok "$CMD_EXPLAIN"
check "mantis-disable has 2 frontmatter fences"  fence_count_ok "$CMD_DISABLE"

# ---- 3. Semantic /mantis-disable invocation ---------------------------------
# Point override.py at a temp state file so we don't touch the real one.

TMP_STATE="$(mktemp -t mantis-overrides-XXXXXX.json)"
trap 'rm -f "$TMP_STATE"' EXIT

python plugins/mantis-preference/scripts/override.py \
  --dev "test-harness-dev" \
  --rule "PY-M1-001" \
  --state "$TMP_STATE" \
  disable >/dev/null

# Validate the record is present, shape is correct, dates are sane.
invocation_ok() {
  python - "$TMP_STATE" <<'PY'
import sys, json, pathlib
from datetime import datetime, timezone, timedelta

p = pathlib.Path(sys.argv[1])
entries = json.loads(p.read_text(encoding="utf-8"))
assert isinstance(entries, list) and len(entries) == 1, f"expected 1 entry, got {entries!r}"
e = entries[0]
for k in ("dev_id", "rule_id", "disabled_at", "reprompt_at"):
    assert k in e, f"missing key {k}"
assert e["dev_id"] == "test-harness-dev", e["dev_id"]
assert e["rule_id"] == "PY-M1-001", e["rule_id"]

def _parse(s):
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s)

disabled_at = _parse(e["disabled_at"])
reprompt_at = _parse(e["reprompt_at"])
today = datetime.now(timezone.utc)

# disabled_at is today (UTC), within a small clock-skew tolerance.
assert abs((today - disabled_at).total_seconds()) < 120, \
    f"disabled_at not ~now: {disabled_at}"

# reprompt_at is ~90 days out.
delta = (reprompt_at - disabled_at).days
assert 89 <= delta <= 91, f"reprompt delta not ~90 days: {delta}"
PY
}

check "/mantis-disable PY-M1-001 writes a valid override record" invocation_ok

# ---- summary ----------------------------------------------------------------

echo
printf "  passed: %d   failed: %d\n" "$pass" "$fail"
exit "$fail"
