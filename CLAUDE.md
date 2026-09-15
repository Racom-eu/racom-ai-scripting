# RACOM Claude Sandbox — project instructions

This repository is (a) the RACOM dev-container that runs Claude Code and (b) a Claude Code
**plugin** named `racom` (`.claude-plugin/plugin.json`) whose skills live under `skills/`.
It is loaded **in place** (no copy) as a "skills-dir" plugin through a symlink in `~/.claude/skills/`.

## Skills

| skill | invoke | purpose |
|-------|--------|---------|
| `skills/api/` | `/racom:api` (auto-triggers on RipEx2/RA2/device-API topics) | RipEx2 / RA2 device HTTPS RPC API: `ra2` CLI, `rr_ra2_mgmt` library (submodule), API-docs lookup |
| `skills/scripting/` | `/racom:scripting` (auto-triggers on Scripting / scripts / launcher / triggers) | Scripts that run inside the device: `scripting` CLI (new, check, deploy, run, logs, modules), templates, runtime contract; builds on the two skills below |
| `skills/netconf/` | `/racom:netconf` (auto-triggers on NETCONF/YANG topics) | RipEx2 / RA2 over NETCONF: `netconf` CLI (YANG tree/find, get/set, edit-config, <get>, raw RPC), `rr_netconf_mgmt` library (submodule) |

Loading (persisted in `~/.claude` = `./.claude-home`, created by `.devcontainer/post-create.sh`):

```bash
mkdir -p ~/.claude/skills && ln -sfn /workspaces/workspace ~/.claude/skills/racom   # auto-loads as racom@skills-dir
claude plugin details racom            # verify: "Skills (1)  api"
claude --plugin-dir /workspaces/workspace                                           # alternative: one session only
```

Do **not** install this repo through `claude plugin marketplace add` / `claude plugin install`:
that copies the whole plugin root (including `.claude-home`, `.venv`, `.prime`) into the plugin cache.

## Conventions

- Device credentials belong in `.env.ra2` (git-ignored; template `.env.ra2.example`) or `RA2_*`
  env vars — never in files that are committed, in memory notes, or in chat output.
- `skills/api/ra2_mgmt` and `skills/netconf/rac_netconf_mgmt` are git submodules (GitHub
  `Racom-eu/*`). Do not edit them from here; bump the pointer and rerun the skill's
  `scripts/gen_catalog.py` so `references/methods.md` stays in sync (`--check` verifies).
- Anything that changes device state (config save, firmware, reboot, reset, users, keyring)
  requires an explicit user confirmation first — see the Rules section of `skills/api/SKILL.md`.
- Python: the `ra2` CLI is stdlib-only and runs on system `python3` (>= 3.11). The `netconf` CLI
  needs `bash skills/netconf/scripts/setup.sh` once per container (apt deps, libyang C 4.2.2 from
  source, `.venv` with ncclient/libyang/lxml). Both skills share `<repo>/.venv`.
- Device state changes over either protocol need explicit user confirmation; both protocols edit the
  same configuration (`RR_*` keys), so one can verify the other.
- `docs/scripting/` is the Scripting documentation the `scripting` skill is derived from; keep both in sync when the
  launcher contract changes (`skills/scripting/references/runtime.md` is the condensed form).
- The in-device script feature is called **Scripting** (skill, CLI, docs, prose). "SDK" appears only in
  device internals that we do not name: YANG `rr-sdk-launcher`, the `sdk_launcher` daemon/module,
  CNF `RR_Sdk_Enable` (`main/SDK`), `/mnt/data/sdk/`.
- New skills go to `skills/<name>/SKILL.md`; they are picked up by the `racom` plugin automatically.
- `docs/scripting/` is our own documentation for scripts running in the device (RA2 API vs NETCONF
  access, runtime contract, examples). `docs/rac_sw_doc` is an external submodule slated for removal;
  do not add content there and do not link into it from `docs/scripting`.
