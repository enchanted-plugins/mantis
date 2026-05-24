#!/usr/bin/env bash
# Thin per-plugin wrapper. Canonical router: shared/hooks/dispatch.sh.
# Phase 2 stub: dispatches to the "unknown command" branch (fail-open NOTE).
exec bash "$(dirname "$0")/../../../shared/hooks/dispatch.sh" "$@"
