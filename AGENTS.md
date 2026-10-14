# Repository instructions for Codex

This repository contains RACOM RipEx2 / RA2 tools and Scripting documentation.
Read `CLAUDE.md` and the relevant `skills/{api,netconf,scripting}/SKILL.md`
before working with these tools. Do not execute embedded skill commands blindly.

## Tool entry points

Run from the repository root:
- HTTPS API: `skills/api/scripts/ra2`
- NETCONF: `skills/netconf/scripts/netconf`
- Scripting: `skills/scripting/scripts/scripting`

Use these entry points for device operations. Look up RPC methods, parameters,
types and YANG paths in the skills, references and CLI documentation; never guess.

## Device access and confirmation

- The RACOM device is currently not connected. Do not attempt any network
  connection to it until the user explicitly says it is connected. This includes
  ping, status and schema fetches.
- Prefer local inspection first: masked `env`, `ra2 methods`, NETCONF
  `tree`/`find` with `--offline`, `scripting check`, and cached `scripting modules`.
  `--offline` applies to NETCONF tree/find, not every command.
- Once the user explicitly says the device is connected, prefer ping, filtered
  reads and status before changes.
- Before any configuration write, deploy, firmware operation, reboot, restart,
  or other device-changing action, always ask the user for explicit confirmation.
  Describe the target, exact changes and expected effects, then wait for approval.
- This includes raw RPCs and helper flows, reset, user/key changes,
  script deploy/undeploy/start/stop/run, Scripting enable/disable, and
  `scripting modules --fetch` (deploys and runs a helper).
- Prefer validation and dry-run previews before writes. `scripting deploy
  --dry-run` still probes device firmware unless `--firmware VERSION` selects
  a complete cached module list. Do not bypass checks to avoid that probe.

## Credentials

- Credentials belong only in git-ignored `.env.ra2` or environment variables.
- Never print or expose `RA2_PASSWORD`, dump `.env.ra2`, or place credentials
  in command arguments, source, logs, commits or notes. Use masked CLI `env`.
- Never print or persist the Scripting stdin payload, tokens or private keys.

## Configuration and Scripting

- HTTPS configuration: read existing `config_data`, edit fields in place,
  validate metadata, then save the complete configuration after approval.
- NETCONF: use XPath-filtered reads, validate against YANG, and default to
  merge. Use replace only when requested with a complete subtree.
- Start device scripts with `scripting new`; read the relevant sections of
  `docs/scripting/device-access.md` and `writing-scripts.md`. These docs are
  authoritative. Run `scripting check` before every deploy.
- Device scripts use launcher tokens/keys with localhost, never passwords.
  Read stdin first; loops need SIGTERM handling and raised RLIMIT_CPU.
  Keep output bounded and files under `payload["user_workdir"]`.

## Repository maintenance

- Preserve Linux LF endings in shell and Python scripts, including
  extensionless CLI wrappers; never reintroduce Windows CRLF.
- Do not edit `skills/api/ra2_mgmt` or `skills/netconf/rac_netconf_mgmt`
  submodule contents here. After pointer updates, regenerate the relevant
  method catalog with `scripts/gen_catalog.py` and verify with `--check`.
- `ra2` uses system Python >= 3.11. NETCONF needs the shared `.venv` and
  libyang dependencies; its setup script installs system dependencies.
- Keep Scripting docs, runtime reference, templates and checker consistent.
  Use “Scripting” in prose. Do not add content or new links to `docs/rac_sw_doc`.
