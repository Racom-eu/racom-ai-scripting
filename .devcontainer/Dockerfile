# syntax=docker/dockerfile:1
# =============================================================================
#  RACOM - Claude Code Development Sandbox
# -----------------------------------------------------------------------------
#  Component : racom/claude-sandbox (container image)
#  Purpose   : Reproducible, non-root dev container with Claude Code preinstalled
#  Owner     : RACOM - Development Tooling
#  License   : Proprietary - (c) 2025 RACOM s.r.o. All rights reserved.
#  Notes     : No credentials are baked into this image. Authentication is
#              performed interactively by the developer inside the container.
# =============================================================================

FROM mcr.microsoft.com/devcontainers/base:ubuntu-24.04

ARG NODE_VERSION=22
ARG USERNAME=vscode
ARG RACOM_ENV_NAME="RACOM Claude Sandbox"

LABEL org.opencontainers.image.vendor="RACOM" \
      org.opencontainers.image.title="RACOM Claude Sandbox" \
      org.opencontainers.image.description="RACOM standard development container with Claude Code preinstalled." \
      org.opencontainers.image.licenses="Proprietary" \
      com.racom.component="claude-sandbox" \
      com.racom.tier="development"

# ---- Base tooling -----------------------------------------------------------
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        git \
        gnupg \
        jq \
        less \
        ripgrep \
        unzip \
        vim \
        zsh \
    && rm -rf /var/lib/apt/lists/*

# ---- Native deps for skills/netconf (libyang C library + headers) ----------
# The Python `libyang` binding pinned by rac_netconf_mgmt needs libyang soversion >= 4.2.2;
# Ubuntu 24.04 ships 2.1.x, so build CESNET/libyang from source. Also provides `yanglint`.
ARG LIBYANG_VERSION=4.2.2
RUN apt-get update && export DEBIAN_FRONTEND=noninteractive \
    && apt-get install -y --no-install-recommends python3-dev cmake libpcre2-dev libssh-dev \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/CESNET/libyang/archive/refs/tags/v${LIBYANG_VERSION}.tar.gz" | tar xz -C /tmp \
    && cmake -S "/tmp/libyang-${LIBYANG_VERSION}" -B /tmp/libyang-build -DCMAKE_BUILD_TYPE=Release \
         -DENABLE_TESTS=OFF -DENABLE_VALGRIND_TESTS=OFF -DCMAKE_INSTALL_PREFIX=/usr/local \
    && make -C /tmp/libyang-build -j"$(nproc)" && make -C /tmp/libyang-build install && ldconfig \
    && rm -rf /tmp/libyang-*

# ---- Node.js (required by Claude Code) --------------------------------------
RUN curl -fsSL https://deb.nodesource.com/setup_${NODE_VERSION}.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Let the non-root user install global npm packages without sudo.
ENV NPM_CONFIG_PREFIX=/usr/local/npm-global
ENV PATH=${NPM_CONFIG_PREFIX}/bin:${PATH}

# All root-owned directory creation MUST happen here, before `USER` drops
# privileges. /commandhistory lives at / and cannot be created later.
RUN mkdir -p ${NPM_CONFIG_PREFIX} /commandhistory /home/${USERNAME}/.claude \
    && touch /commandhistory/.bash_history \
    && chown -R ${USERNAME}:${USERNAME} \
        ${NPM_CONFIG_PREFIX} /commandhistory /home/${USERNAME}/.claude

USER ${USERNAME}

# ---- Claude Code ------------------------------------------------------------
# Installed globally at build time. NO credentials or API keys are baked in;
# run `claude` inside the container and log in interactively.
RUN npm install -g @anthropic-ai/claude-code \
    && claude --version

# ---- Python tooling (uv) ----------------------------------------------------
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH=/home/${USERNAME}/.local/bin:${PATH}

ENV SHELL=/bin/bash \
    RACOM_ENV_NAME="${RACOM_ENV_NAME}" \
    RACOM_SANDBOX=1

WORKDIR /workspaces
