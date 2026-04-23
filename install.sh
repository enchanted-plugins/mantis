#!/usr/bin/env bash
# Lich installer. Sub-plugins coordinate through the enchanted-mcp event bus;
# the `full` meta-plugin pulls them all in via one dependency-resolution pass.
set -euo pipefail

REPO="https://github.com/enchanted-plugins/lich"
PLUGIN_HOME_DIR="$HOME/.claude/plugins/lich"

step() { printf "\n\033[1;36m▸ %s\033[0m\n" "$*"; }
ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m!\033[0m %s\n" "$*" >&2; }

step "Lich installer"

# 1. Clone the monorepo so shared/scripts/*.py are available locally.
#    Plugins themselves are served via the marketplace command below.
if [[ -d "$PLUGIN_HOME_DIR/.git" ]]; then
  git -C "$PLUGIN_HOME_DIR" pull --ff-only --quiet
  ok "Updated existing clone at $PLUGIN_HOME_DIR"
else
  git clone --depth 1 --quiet "$REPO" "$PLUGIN_HOME_DIR"
  ok "Cloned to $PLUGIN_HOME_DIR"
fi

# 2. Pre-flight git check — Lich's hooks and scripts require git.
if ! command -v git >/dev/null 2>&1; then
  warn "git not found on PATH — Lich requires git"
  exit 1
fi
ok "git present"

# 3. Platform check — M5 Bounded Subprocess Dry-Run requires POSIX `resource` module.
case "$(uname -s 2>/dev/null || echo Windows)" in
  Linux|Darwin|FreeBSD|OpenBSD)
    ok "POSIX platform detected — M5 sandbox available"
    ;;
  *)
    warn "Non-POSIX platform — M5 Bounded Subprocess Dry-Run will skip with 'platform-unsupported' verdict. M1/M2/M6/M7 engines unaffected. Windows support tracked for Phase 2 (Job Objects backend)."
    ;;
esac

# 4. Credential helper check — fast-path for GitHub when gh auth is configured.
if git credential-manager --version >/dev/null 2>&1; then
  ok "git-credential-manager detected"
elif command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  ok "gh auth detected (fast-path for GitHub)"
else
  warn "No credential helper detected. Install git-credential-manager or run 'gh auth login'."
fi

cat <<'EOF'

─────────────────────────────────────────────────────────────────────────
  Lich ships as a 7-plugin marketplace. Each sub-plugin owns one named
  engine (M1, M2, M5, M6, M7) or one orthogonal concern (language adapter,
  verdict synthesizer). The `full` meta-plugin lists all 7 as dependencies
  so one install pulls in the whole chain.
─────────────────────────────────────────────────────────────────────────

  Finish in Claude Code with TWO commands:

    /plugin marketplace add enchanted-plugins/lich
    /plugin install full@lich

  That installs every sub-plugin via dependency resolution. To cherry-pick
  a single sub-plugin instead, use e.g. `/plugin install lich-core@lich`.

  Verify with:   /plugin list
  Expected:      full + 7 sub-plugins installed under the lich marketplace.

  Upstream context:
    - Hydra owns security (CWE findings) — Lich never duplicates R3.
    - Crow owns change classification — Lich consumes V1/V2.
    - Pech owns cost tracking — Lich downshifts M7 judge under budget pressure.

EOF
