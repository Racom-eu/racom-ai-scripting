#!/usr/bin/env bash
# =============================================================================
#  RACOM - Claude Code Development Sandbox
# -----------------------------------------------------------------------------
#  Component : racom-claude-sandbox (postCreateCommand hook)
#  Owner     : RACOM - Development Tooling
#  License   : Proprietary - (c) 2025 RACOM s.r.o. All rights reserved.
#  Purpose   : Prepare the persistent memory mount and report the environment.
# =============================================================================
set -euo pipefail

CLAUDE_HOME="/home/vscode/.claude"
RACOM_ENV_NAME="${RACOM_ENV_NAME:-RACOM Claude Sandbox}"

# /commandhistory is a named volume and starts root-owned.
sudo chown -R "$(id -u):$(id -g)" /commandhistory 2>/dev/null || true

# $CLAUDE_HOME is a BIND MOUNT of ./.claude-home on the host: this is the
# persistent memory. Do not recursively chown it (that would rewrite host
# ownership); just make sure it is writable and has the expected layout.
mkdir -p "$CLAUDE_HOME/projects" "$CLAUDE_HOME/todos"

if ! touch "$CLAUDE_HOME/.racom-write-test" 2>/dev/null; then
  echo "=============================================================="
  echo " WARNING: $CLAUDE_HOME is NOT writable by $(id -un) (uid $(id -u))."
  echo " Claude Code logins and transcripts will NOT persist (EACCES)."
  echo " Rootless podman? On the HOST run:"
  echo "     podman unshare chown -R 1000:1000 ./.claude-home"
  echo " or start with --userns=keep-id:uid=1000,gid=1000."
  echo " Details: bash .devcontainer/doctor.sh"
  echo "=============================================================="
else
  rm -f "$CLAUDE_HOME/.racom-write-test"
fi

# Seed an empty global memory file once, so there is an obvious place to write
# long-lived project knowledge. Never overwrite an existing one.
if [ ! -e "$CLAUDE_HOME/CLAUDE.md" ]; then
  cat > "$CLAUDE_HOME/CLAUDE.md" <<'EOF'
# RACOM | Persistent memory

This file is the global CLAUDE.md for the RACOM Claude Sandbox.
It is stored on the host at ./.claude-home/CLAUDE.md and survives
container rebuilds, image deletion, and machine restarts.

Use `#` in Claude Code to append a memory here.

## Conventions
- Internal RACOM code and customer data stay inside the sandbox.
- Do not paste credentials into memory files.
EOF
  echo "Created $CLAUDE_HOME/CLAUDE.md"
fi

# Expose this repository as the Claude Code plugin "racom" (skills/<name>/SKILL.md -> /racom:<name>).
# A symlink in ~/.claude/skills/ makes Claude Code load it IN PLACE as "racom@skills-dir" - no copy,
# no login needed, survives rebuilds because ~/.claude is the persistent bind mount.
mkdir -p "$CLAUDE_HOME/skills"
if [ "$(readlink "$CLAUDE_HOME/skills/racom" 2>/dev/null)" != "/workspaces/workspace" ]; then
  ln -sfn /workspaces/workspace "$CLAUDE_HOME/skills/racom" \
    && echo "[RACOM] Linked plugin racom -> $CLAUDE_HOME/skills/racom (skills: /racom:api)." \
    || echo "[RACOM] Could not link the racom plugin (run: ln -sfn /workspaces/workspace ~/.claude/skills/racom)."
fi

echo "[RACOM] ${RACOM_ENV_NAME} provisioned."
echo "[RACOM] Claude Code: $(claude --version 2>/dev/null || echo 'not found')"
echo "[RACOM] Node:        $(node --version)"
echo "[RACOM] Memory:      $CLAUDE_HOME  <- bind mount of ./.claude-home on the host"
echo
echo "No credentials are provisioned in this image."
echo "Run 'claude' in the terminal and complete the interactive login when you are ready."
