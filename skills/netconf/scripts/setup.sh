#!/usr/bin/env bash
# =============================================================================
#  skills/netconf — environment setup (REQUIRED once per container)
# -----------------------------------------------------------------------------
#  Unlike skills/api, the NETCONF library (rr_netconf_mgmt) has native deps:
#    * ncclient[libssh]  -> ssh-python wheel (needs libssh at runtime)
#    * lxml              -> wheel
#    * libyang 4.0.0     -> Python binding compiled against the libyang C
#                           library, soversion >= 4.2.2 (Ubuntu 24.04 ships
#                           2.1.x, so we build v4.2.2 from source into /usr/local)
#  This script is idempotent: every step is skipped when already satisfied.
#    1. git submodule init          (skills/netconf/rac_netconf_mgmt)
#    2. apt: python3-dev cmake libpcre2-dev libssh-dev      (sudo)
#    3. libyang C v${LIBYANG_VERSION} -> /usr/local (+ yanglint)  (sudo)
#    4. uv venv .venv at the repo root with rr_netconf_mgmt (editable) + pytest
#    5. smoke test (library import, libyang context, unit tests)
#  Usage: bash skills/netconf/scripts/setup.sh [--no-tests]
# =============================================================================
set -euo pipefail

LIBYANG_VERSION="${LIBYANG_VERSION:-4.2.2}"      # must satisfy the pinned python binding (see pyproject.toml)
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_DIR="$(cd "$SKILL_DIR/../.." && pwd)"
LIB_DIR="$SKILL_DIR/rac_netconf_mgmt"
VENV="$REPO_DIR/.venv"

say() { echo "[netconf] $*"; }

say "skill dir : $SKILL_DIR"

# 1) submodule
if [ ! -f "$LIB_DIR/pyproject.toml" ]; then
  say "submodule empty -> git submodule update --init --recursive"
  git -C "$REPO_DIR" submodule update --init --recursive -- "$LIB_DIR"
fi

# 2) system packages
PYVER="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
NEED=()
dpkg -s "python${PYVER}-dev" >/dev/null 2>&1 || NEED+=("python${PYVER}-dev")
for p in cmake libpcre2-dev libssh-dev; do dpkg -s "$p" >/dev/null 2>&1 || NEED+=("$p"); done
if [ ${#NEED[@]} -gt 0 ]; then
  say "installing apt packages: ${NEED[*]}"
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "${NEED[@]}"
fi

# 3) libyang C library
have_ly="$(grep -oE 'LY_VERSION "[0-9.]+"' /usr/local/include/libyang/version.h 2>/dev/null | grep -oE '[0-9.]+' || true)"
if [ "$have_ly" != "$LIBYANG_VERSION" ]; then
  say "building libyang C v$LIBYANG_VERSION (have: ${have_ly:-none})"
  BUILD="$(mktemp -d)"
  curl -fsSL "https://github.com/CESNET/libyang/archive/refs/tags/v${LIBYANG_VERSION}.tar.gz" | tar xz -C "$BUILD"
  cmake -S "$BUILD/libyang-${LIBYANG_VERSION}" -B "$BUILD/build" -DCMAKE_BUILD_TYPE=Release \
        -DENABLE_TESTS=OFF -DENABLE_VALGRIND_TESTS=OFF -DCMAKE_INSTALL_PREFIX=/usr/local >"$BUILD/cmake.log"
  make -C "$BUILD/build" -j"$(nproc)" >"$BUILD/make.log"
  sudo rm -f /usr/local/lib/libyang.so.*                 # drop an older soversion
  sudo make -C "$BUILD/build" install >"$BUILD/install.log"
  sudo ldconfig
  rm -rf "$BUILD"
fi
say "libyang C : $(grep -oE 'LY_VERSION "[0-9.]+"' /usr/local/include/libyang/version.h | grep -oE '[0-9.]+')  (yanglint: $(command -v yanglint || echo missing))"

# 4) venv
command -v uv >/dev/null 2>&1 || { say "uv not found (curl -LsSf https://astral.sh/uv/install.sh | sh)"; exit 1; }
[ -x "$VENV/bin/python" ] || { say "creating $VENV"; uv venv "$VENV" -q; }
if ! "$VENV/bin/python" -c "import libyang, ncclient, rr_netconf_mgmt" >/dev/null 2>&1; then
  say "installing rr_netconf_mgmt (+ libyang binding, ncclient[libssh], lxml) into $VENV"
  uv pip install -q --python "$VENV/bin/python" --no-cache -e "$LIB_DIR" pytest
fi

# 5) smoke test
"$VENV/bin/python" - <<'EOF'
import libyang, ncclient, ssh, rr_netconf_mgmt  # noqa: F401
ctx = libyang.Context(); ctx.destroy()
print("[netconf] python    : ok (libyang binding, ncclient + libssh, rr_netconf_mgmt)")
EOF
if [ "${1:-}" != "--no-tests" ]; then
  "$VENV/bin/python" -m pytest "$LIB_DIR/tests" -q -p no:cacheprovider 2>&1 | tail -1 | sed 's/^/[netconf] tests     : /'
fi
"$SKILL_DIR/scripts/netconf" methods >/dev/null 2>&1 && say "cli       : ok ($SKILL_DIR/scripts/netconf)" || say "cli       : NOT working — check $SKILL_DIR/scripts/netconf"
"$VENV/bin/python" "$SKILL_DIR/scripts/gen_catalog.py" --check >/dev/null 2>&1 || say "note      : references/methods.md is stale -> $VENV/bin/python $SKILL_DIR/scripts/gen_catalog.py"
if [ -f "$REPO_DIR/.env.ra2" ]; then
  say "device    : $("$SKILL_DIR/scripts/netconf" --compact env 2>/dev/null || echo 'see .env.ra2')"
else
  say "device    : no .env.ra2 yet -> cp $REPO_DIR/.env.ra2.example $REPO_DIR/.env.ra2 (shared with skills/api)"
fi
say "done."
