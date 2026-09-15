#!/usr/bin/env bash
# =============================================================================
#  skills/api — developer setup (OPTIONAL)
# -----------------------------------------------------------------------------
#  The `ra2` CLI and the rr_ra2_mgmt library are stdlib-only and run on the
#  system python3 (>= 3.11) with no installation. This script is only needed to:
#    * initialise the ra2_mgmt git submodule if it is empty,
#    * create a uv-managed virtualenv (.venv at the repo root) with the library
#      installed editable plus pytest, so you can run the library's test-suite
#      and use `from rr_ra2_mgmt.device import Device` in ad-hoc scripts
#      without touching sys.path,
#    * verify everything with a smoke test.
#  Usage: bash skills/api/scripts/setup.sh [--no-venv]
# =============================================================================
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$SKILL_DIR/../.." && pwd)"
LIB_DIR="$SKILL_DIR/ra2_mgmt"

echo "[ra2] skill dir : $SKILL_DIR"
echo "[ra2] repo dir  : $REPO_DIR"

# 1) submodule
if [ ! -f "$LIB_DIR/pyproject.toml" ]; then
  echo "[ra2] submodule empty -> git submodule update --init --recursive"
  git -C "$REPO_DIR" submodule update --init --recursive
fi

# 2) python
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "[ra2] python3 not found"; exit 1; }
"$PY" - <<'EOF'
import sys
assert sys.version_info >= (3, 11), f"python >= 3.11 required, found {sys.version}"
print(f"[ra2] python    : {sys.version.split()[0]} ({sys.executable})")
EOF

# 3) optional venv for tests / ad-hoc imports
if [ "${1:-}" != "--no-venv" ]; then
  if ! command -v uv >/dev/null 2>&1; then
    echo "[ra2] uv not found; skipping venv (install: curl -LsSf https://astral.sh/uv/install.sh | sh)"
  else
    if [ ! -x "$REPO_DIR/.venv/bin/python" ]; then
      echo "[ra2] creating $REPO_DIR/.venv"
      uv venv "$REPO_DIR/.venv" -q
    fi
    uv pip install -q --python "$REPO_DIR/.venv/bin/python" -e "$LIB_DIR" pytest
    echo "[ra2] venv      : $REPO_DIR/.venv (rr_ra2_mgmt editable + pytest)"
    echo "[ra2] tests     : $REPO_DIR/.venv/bin/python -m pytest $LIB_DIR/tests -q"
  fi
fi

# 4) smoke test — no device needed
"$SKILL_DIR/scripts/ra2" methods device_ping >/dev/null && echo "[ra2] cli       : ok ($SKILL_DIR/scripts/ra2)"
"$PY" "$SKILL_DIR/scripts/gen_catalog.py" --check >/dev/null 2>&1 || echo "[ra2] note      : references/methods.md is stale -> python3 $SKILL_DIR/scripts/gen_catalog.py"

if [ -f "$REPO_DIR/.env.ra2" ]; then
  echo "[ra2] device    : $("$SKILL_DIR/scripts/ra2" --compact env)"
else
  echo "[ra2] device    : no .env.ra2 yet -> cp $REPO_DIR/.env.ra2.example $REPO_DIR/.env.ra2 and fill in RA2_HOST/RA2_USER/RA2_PASSWORD"
fi
echo "[ra2] done."
