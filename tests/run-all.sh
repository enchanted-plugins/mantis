#!/usr/bin/env bash
# Test runner — iterates every sub-plugin's tests/ directory and runs its tests.
# Plugin-agnostic: adds tests per sub-plugin as you build.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

red() { printf "\033[31m%s\033[0m" "$*"; }
grn() { printf "\033[32m%s\033[0m" "$*"; }
ylw() { printf "\033[33m%s\033[0m" "$*"; }

pass=0
fail=0
skipped=0

for plugin_dir in plugins/*/; do
  plugin_name="$(basename "$plugin_dir")"
  test_dir="$plugin_dir/tests"

  if [[ ! -d "$test_dir" ]]; then
    printf "  %s %s — no tests/ directory\n" "$(ylw '○')" "$plugin_name"
    skipped=$((skipped + 1))
    continue
  fi

  # Look for test-*.sh scripts
  shopt -s nullglob
  tests=("$test_dir"/test-*.sh)
  shopt -u nullglob

  if [[ ${#tests[@]} -eq 0 ]]; then
    printf "  %s %s — empty tests/ directory\n" "$(ylw '○')" "$plugin_name"
    skipped=$((skipped + 1))
    continue
  fi

  for t in "${tests[@]}"; do
    if bash "$t" >/dev/null 2>&1; then
      printf "  %s %s/%s\n" "$(grn '✓')" "$plugin_name" "$(basename "$t")"
      pass=$((pass + 1))
    else
      printf "  %s %s/%s\n" "$(red '✗')" "$plugin_name" "$(basename "$t")"
      fail=$((fail + 1))
    fi
  done
done

echo
printf "  passed: %d   failed: %d   skipped: %d\n" "$pass" "$fail" "$skipped"

if [[ $pass -eq 0 && $fail -eq 0 ]]; then
  echo
  echo "  no tests yet — add test-*.sh scripts to plugins/<name>/tests/ as you build."
  exit 0
fi

exit "$fail"
