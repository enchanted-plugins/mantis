#!/usr/bin/env bash
# Thin per-plugin wrapper. Canonical router: shared/hooks/dispatch.sh.
# All Lich sub-plugins use an identical wrapper; only the argv ($1 command
# name, supplied by the plugin's hooks.json) distinguishes them.
exec bash "$(dirname "$0")/../../../shared/hooks/dispatch.sh" "$@"
