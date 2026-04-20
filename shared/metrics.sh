#!/usr/bin/env bash
# Generic metrics helpers — append JSONL events to a per-plugin metrics file.
# Plugin-agnostic: add plugin-specific counters/timers in your sub-plugin's hooks.

# emit_metric <metrics_file> <event_type> [key=value ...]
# Writes one JSONL line with: {ts, event, key1: value1, ...}
emit_metric() {
  local file="$1"; shift
  local event="$1"; shift
  local ts
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

  # Build a JSON object using jq. Falls back to printf if jq unavailable.
  if command -v jq >/dev/null 2>&1; then
    local args=(--arg ts "$ts" --arg event "$event")
    local filter='{ts: $ts, event: $event}'
    for kv in "$@"; do
      local k="${kv%%=*}"
      local v="${kv#*=}"
      args+=(--arg "k_$k" "$v")
      filter="$filter + {\"$k\": \$k_$k}"
    done
    jq -cn "${args[@]}" "$filter" >> "$file"
  else
    # Fallback — best-effort JSON, no escaping. jq is required for correctness.
    printf '{"ts":"%s","event":"%s"}\n' "$ts" "$event" >> "$file"
  fi
}

# rotate_if_too_big <file> <max_bytes>
# Moves file to file.1 when size exceeds max_bytes. Keeps exactly one rotation.
rotate_if_too_big() {
  local file="$1"
  local max="$2"
  [[ -f "$file" ]] || return 0
  local size
  size="$(wc -c < "$file" 2>/dev/null || echo 0)"
  if (( size > max )); then
    mv "$file" "$file.1"
  fi
}
