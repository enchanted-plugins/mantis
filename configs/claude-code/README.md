# Claude Code configs — {{PluginName}}

Optional per-user Claude Code settings snippets for {{PluginName}}. These are suggestions, not requirements — the plugin works without any settings changes.

## `settings.json` patterns

### Allow-list the sub-plugin Bash commands

If you install `{{PLUGIN_SLUG}}` and want to skip the permission prompt for its known-safe Bash commands, add them to your user or project `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash({{TODO: list specific shell commands this plugin invokes, e.g. 'git status', 'jq ...'}})"
    ]
  }
}
```

### Hook integration

{{PluginName}} sub-plugins install their own hooks via `/plugin install`. You do not need to copy hook definitions into your user settings — the plugin manifests handle registration.

## Status line (optional)

If you use Claude Code's status line, {{PluginName}} surfaces per-sub-plugin state at `plugins/<name>/state/` that you can read via `statusLine.command`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "bash -c 'cat plugins/{{SUB_PLUGIN_1_NAME}}/state/status 2>/dev/null || echo {{PluginName}}'"
  }
}
```

## Reference

See Claude Code documentation for full `settings.json` schema. Every {{PluginName}} snippet here is optional; the plugin is fully functional with default settings.
