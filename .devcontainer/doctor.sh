#!/usr/bin/env bash
# =============================================================================
#  RACOM - Claude Code Development Sandbox | memory mount diagnostics
# -----------------------------------------------------------------------------
#  Owner   : RACOM - Development Tooling
#  License : Proprietary - (c) 2025 RACOM s.r.o. All rights reserved.
#  Usage   : bash .devcontainer/doctor.sh   (run INSIDE the container)
# =============================================================================
set -uo pipefail
CLAUDE_HOME="/home/vscode/.claude"

echo "[RACOM] user:  $(id -un) uid=$(id -u) gid=$(id -g)"
echo "[RACOM] mount: $(stat -c '%n owner=%U(%u):%G(%g) mode=%a' "$CLAUDE_HOME" 2>/dev/null || echo "$CLAUDE_HOME MISSING")"

if touch "$CLAUDE_HOME/.racom-write-test" 2>/dev/null; then
  rm -f "$CLAUDE_HOME/.racom-write-test"
  echo "[RACOM] write: OK - persistent memory is functional."
else
  echo "[RACOM] write: FAILED (EACCES) - login and transcripts will NOT persist."
  echo
  echo "  Rootless podman maps your host user to container root, so uid 1000"
  echo "  (vscode) cannot write the bind mount. Fix on the HOST, then rebuild:"
  echo
  echo "    podman unshare chown -R 1000:1000 ./.claude-home"
  echo
  echo "  Or map your host user onto vscode (preferred, keeps host ownership):"
  echo "    add  --userns=keep-id:uid=1000,gid=1000  to runArgs / use"
  echo "    docker-compose.podman.yml"
fi
