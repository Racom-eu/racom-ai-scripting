#!/usr/bin/env bash
# =============================================================================
#  RACOM - Claude Code Development Sandbox
# -----------------------------------------------------------------------------
#  Component : racom-claude-sandbox (postStartCommand hook)
#  Owner     : RACOM - Development Tooling
#  License   : Proprietary - (c) 2025 RACOM s.r.o. All rights reserved.
#  Purpose   : Print the branded banner and login status on every start.
# =============================================================================
set -uo pipefail

RACOM_ENV_NAME="${RACOM_ENV_NAME:-RACOM Claude Sandbox}"

cat <<'BANNER'
  ____      _    ____ ___  __  __
 |  _ \    / \  / ___/ _ \|  \/  |
 | |_) |  / _ \| |  | | | | |\/| |
 |  _ <  / ___ \ |__| |_| | |  | |
 |_| \_\/_/   \_\____\___/|_|  |_|
BANNER

echo "  ${RACOM_ENV_NAME}"
echo "  Claude Code $(claude --version 2>/dev/null || echo 'unavailable') | workspace: $(pwd)"

if [ -e "/home/vscode/.claude/.credentials.json" ]; then
  echo "  Session:   existing login found in persistent memory."
else
  echo "  Session:   not logged in - run 'claude' to authenticate."
fi
echo
