#!/usr/bin/env bash
# Generic sanitizers — safe string handling for hooks and state files.

# sanitize_for_json <input>
# Escapes characters that would break JSON: backslash, double quote, newline, tab.
# Emits the escaped string on stdout. Use when constructing JSON without jq.
sanitize_for_json() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}

# sanitize_path <input>
# Rejects paths with traversal (..) or absolute prefixes; returns empty on rejection.
# Use before writing any file whose path came from tool output or user input.
sanitize_path() {
  local p="$1"
  if [[ "$p" == *".."* ]] || [[ "$p" == /* ]]; then
    return 1
  fi
  printf '%s' "$p"
}

# sanitize_slug <input>
# Normalizes to lowercase-kebab: letters, digits, hyphens only. Rejects empty result.
sanitize_slug() {
  local s="$1"
  s="$(printf '%s' "$s" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9-' '-' | tr -s '-')"
  s="${s#-}"; s="${s%-}"
  [[ -z "$s" ]] && return 1
  printf '%s' "$s"
}
