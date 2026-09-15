# RACOM | Claude Code Development Sandbox

> **Owner:** RACOM — Development Tooling
> **Component:** `racom-claude-sandbox`
> **License:** Proprietary — © 2025 RACOM s.r.o. All rights reserved.

The RACOM standard development container with
[Claude Code](https://docs.anthropic.com/en/docs/claude-code) pre-installed.
**No login, API key, or token is seeded** — you authenticate yourself after starting the container.

## Naming conventions

| Artifact          | Name                                                |
|-------------------|-----------------------------------------------------|
| Dev container     | `RACOM Claude Sandbox`                              |
| Container name    | `racom-claude-sandbox-<workspace>` (compose: `racom-claude-sandbox`) |
| Hostname          | `racom-sandbox`                                     |
| Image             | `localhost/racom/claude-sandbox:latest` (local only, never pushed) |
| Compose project   | `racom-claude-sandbox`                              |
| History volume    | `racom-claude-history`                              |
| Image labels      | `com.racom.component`, `com.racom.tier`, `org.opencontainers.image.vendor=RACOM` |
| Env vars          | `RACOM_ENV_NAME`, `RACOM_SANDBOX=1`                 |

## Layout

```
.devcontainer/
  devcontainer.json   # Dev Container definition (VS Code / devcontainer CLI)
  Dockerfile          # Ubuntu 24.04 + Node 22 + Claude Code + uv
  post-create.sh      # Memory bootstrap + environment summary
  post-start.sh       # RACOM banner + login status on every start
  doctor.sh           # Memory-mount diagnostics (run inside the container)
.claude-home/         # PERSISTENT MEMORY (host dir, bind-mounted to ~/.claude)
docker-compose.yml    # Container definition (used by BOTH compose and devcontainer)
.env                  # Local runtime settings (git-ignored)
.env.example          # Shared template for .env
docs/scripting/       # Writing scripts that run in a RipEx2/RA2 device: RA2 API vs NETCONF access, runtime contract, examples
```

## Persistent memory

`./.claude-home` on the host is bind-mounted to `/home/vscode/.claude` in the
container, so everything Claude Code remembers is a real directory you own:

| Path (host)                        | Contents                          |
|------------------------------------|-----------------------------------|
| `.claude-home/CLAUDE.md`           | Global memory / instructions      |
| `.claude-home/projects/`           | Per-project session transcripts   |
| `.claude-home/todos/`              | Task state                        |
| `.claude-home/history.jsonl`       | Prompt history                    |
| `.claude-home/.credentials.json`   | Session auth (created on login)   |

It survives container rebuilds, image deletion, and `docker system prune`, and
you can back it up or move it to another machine by copying the folder.
`.claude-home/` is gitignored because it holds credentials and machine-local state.

Reset memory: `rm -rf .claude-home` (recreated empty on next start).

## Option A — VS Code / Cursor

1. Install the **Dev Containers** extension.
2. `Dev Containers: Reopen in Container`.

## Option B — devcontainer CLI

```bash
npm install -g @devcontainers/cli
devcontainer up --workspace-folder .
devcontainer exec --workspace-folder . bash
```

## Option C — plain Docker

```bash
docker compose build
docker compose run --rm sandbox
```

## Option D — rootless Podman

```bash
cp .env.example .env          # RACOM_USERNS is already set for podman
podman-compose build
podman-compose run --rm sandbox
```

Note: this project ships no dev container *features* — they break rootless
podman builds. Mount the podman socket explicitly if you need nested containers.

## Configuration (`.env`)

`devcontainer.json` is compose-driven, so **both** the dev container and plain
`docker`/`podman compose` read the same project-root `.env`:

| Variable               | Default                                  | Purpose |
|------------------------|------------------------------------------|---------|
| `RACOM_ENV_NAME`       | `RACOM Claude Sandbox`                   | Banner / `$RACOM_ENV_NAME` in shell |
| `RACOM_IMAGE`          | `localhost/racom/claude-sandbox:latest`  | Local image tag |
| `RACOM_CONTAINER_NAME` | `racom-claude-sandbox`                   | Container name |
| `RACOM_HOSTNAME`       | `racom-sandbox`                          | Container hostname |
| `RACOM_USERNS`         | `keep-id:uid=1000,gid=1000`              | **Rootless podman uid mapping.** Set to empty for Docker. |
| `RACOM_SECURITY_OPT`   | `label=disable`                          | SELinux relabel opt-out |

`.env` is git-ignored (it is machine-specific); `.env.example` is tracked.
**Docker users must set `RACOM_USERNS=""`** — `keep-id` is podman-only.

## Logging in

Inside the container:

```bash
claude          # starts the interactive login flow on first run
```

Credentials are written to `/home/vscode/.claude`, i.e. `./.claude-home` on the
host. Your session and memory survive rebuilds, but are never part of the image
and never committed.

To wipe the session only:

```bash
rm -f .claude-home/.credentials.json
```

## What is installed

| Tool        | Version/source                        |
|-------------|---------------------------------------|
| Ubuntu      | 24.04 (devcontainers base)            |
| Node.js     | 22.x (NodeSource)                     |
| Claude Code | `@anthropic-ai/claude-code` (npm, latest at build) |
| uv          | Astral installer                      |
| Extras      | git, gh-ready toolchain, ripgrep, jq, vim, zsh, build-essential |

Global npm packages live in `/usr/local/npm-global`, owned by `vscode`, so
`npm i -g` works without sudo.

## Claude Code plugin & skills

This repository doubles as the Claude Code plugin **`racom`** (`.claude-plugin/plugin.json`);
every `skills/<name>/SKILL.md` becomes `/racom:<name>`. The plugin is loaded in place through a
symlink that `post-create.sh` creates in the persistent memory, or manually:

```bash
mkdir -p ~/.claude/skills && ln -sfn /workspaces/workspace ~/.claude/skills/racom   # auto-loads as racom@skills-dir
claude plugin details racom                                                         # verify
claude --plugin-dir /workspaces/workspace                                           # alternative: this session only
```

Do not `claude plugin install` this repository from a marketplace — that copies the whole plugin
root (including `.claude-home`) into the plugin cache.

| skill | what it gives Claude |
|-------|----------------------|
| `skills/scripting/` | Scripts that run inside the device under the Scripting launcher: the `scripting` CLI (`skills/scripting/scripts/scripting` — new/check/deploy/run/logs), templates and the runtime contract. Documentation in `docs/scripting/`. |
| `skills/netconf/` | RipEx2 / RA2 over NETCONF/YANG: the `netconf` CLI (`skills/netconf/scripts/netconf`), the `rr_netconf_mgmt` library (git submodule). Needs `bash skills/netconf/scripts/setup.sh` once (builds libyang 4.2.2, creates `.venv`). Shares `.env.ra2` with `skills/api`. |
| `skills/api/` | RipEx2 / RA2 device HTTPS RPC API: the `ra2` CLI (`skills/api/scripts/ra2`), the `rr_ra2_mgmt` library (git submodule, `git submodule update --init`), cached API-docs lookup, and reference notes. Device address/credentials go to `.env.ra2` (copy `.env.ra2.example`). |

See `CLAUDE.md` for the conventions that apply when working in this repository.

## Notes

- `.gitignore` and `.dockerignore` exclude `.env*`, `.claude/`, `.claude-home/`,
  and `.claude.json` so secrets cannot be committed or baked into an image layer.
- `updateRemoteUserUID` keeps the container `vscode` user aligned with your host
  UID/GID so the bind-mounted memory stays writable from both sides. If you hit
  permission errors, run `sudo chown -R $(id -u):$(id -g) .claude-home`.
- The container runs as the non-root `vscode` user.
- No container socket is mounted by default, so the sandbox cannot control the
  host runtime. Opt in via the commented `volumes:` entries in `docker-compose.yml`.

## Troubleshooting

**`mkdir: cannot create directory '/commandhistory': Permission denied`**
A root-owned path was created after the Dockerfile switched to `USER vscode`.
All privileged `mkdir`/`chown` work must sit above the `USER` instruction.

**`requested access to the resource is denied` when pulling `racom/claude-sandbox`**
There is no public `racom/claude-sandbox` image — it is built locally. Podman
resolves unqualified names against `docker.io`, so the image is tagged
`localhost/racom/claude-sandbox:latest` with `pull_policy: never`. Build first:
`podman-compose ... build`. A failed build also triggers this, because compose
falls back to pulling; fix the build error first.

**`/login` says "Login successful" but the next message says "Not logged in"**
**and/or `Transcript writes are failing (permission denied - EACCES)`**
Both are the same fault: `/home/vscode/.claude` is not writable, so
`.credentials.json` and transcripts are never written. Diagnose inside the
container with `bash .devcontainer/doctor.sh`. Under rootless podman your host
user maps to container *root*, leaving uid 1000 (`vscode`) unable to write the
bind mount. Fix on the host with either:

```bash
podman unshare chown -R 1000:1000 ./.claude-home   # quick fix
```

or keep host ownership by mapping your user onto `vscode` (already set in
`runArgs` / `docker-compose.podman.yml`):

```
--userns=keep-id:uid=1000,gid=1000
```

Then restart the container and run `/login` once more.

**Files in `.claude-home/` owned by a huge uid (e.g. 493216)**
Rootless Podman subuid mapping. Use `docker-compose.podman.yml`
(`userns_mode: keep-id`), then `podman unshare chown -R 1000:1000 .claude-home`.

**"Reopen in Container" hangs at `? Please select an image:` listing**
**`registry.fedoraproject.org/dev_container_feature_content_temp`**
Dev container *features* build an extra stage from the unqualified image name
`dev_container_feature_content_temp`. Rootless podman cannot resolve short names
and falls back to an interactive registry picker, which blocks the build.
This project therefore ships **no** `features` block — tooling is installed
directly in the Dockerfile. If you add a feature and hit this, either remove it
or disable short-name prompting on the host:

```bash
mkdir -p ~/.config/containers
printf 'short-name-mode="disabled"\n' >> ~/.config/containers/registries.conf
```

For container access from inside the sandbox, mount the podman socket
(see the commented `volumes:` entries in `docker-compose.yml`) instead of using
the `docker-outside-of-docker` feature.
