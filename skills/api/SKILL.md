---
name: api
description: Talk to RACOM RipEx2 / RA2 radio devices over their HTTPS Racom-RPC API. Use whenever the user mentions a RipEx2, RA2, radio modem/router, device configuration (config_data, RR_* keys, station name), device status, statistics, events, monitoring, firmware, users, keyring, diagnostic package, ping/RSS ping/log stream, remote access through an intermediate device, or asks how an API method/model works. Provides the `ra2` CLI (call any RPC, edit config, decode condensed data, look up docs) and the rr_ra2_mgmt Python library.
argument-hint: "[method-or-task]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/ra2 env*), Bash(${CLAUDE_SKILL_DIR}/scripts/ra2 methods*), Bash(${CLAUDE_SKILL_DIR}/scripts/ra2 doc*), Bash(${CLAUDE_SKILL_DIR}/scripts/ra2 ping*)
---

# RipEx2 / RA2 device API

You have a CLI and a Python library for the device API. Prefer the CLI for anything that is one
or two calls; write Python for multi-step flows. Look methods up instead of guessing.

**Current connection** (`ra2 env`; password masked):

!`${CLAUDE_SKILL_DIR}/scripts/ra2 --compact env`

If `host` is `null`, ask for the device address and credentials, or point the user to
`.env.ra2.example` → `.env.ra2` in the repo root (git-ignored). Never print or store passwords.

## Tools

| | |
|---|---|
| `${CLAUDE_SKILL_DIR}/scripts/ra2` | CLI, stdlib-only Python, no install. Alias it: `alias ra2=${CLAUDE_SKILL_DIR}/scripts/ra2` |
| `${CLAUDE_SKILL_DIR}/ra2_mgmt/` | `rr_ra2_mgmt` library (git submodule): `Device` = one method per RPC, `device.helper` = multi-step flows |
| `${CLAUDE_SKILL_DIR}/references/methods.md` | generated catalog of every library method (signature, summary, flags) |
| `${CLAUDE_SKILL_DIR}/references/patterns.md` | recipes: config edit, streams, statistics, firmware, remote access, gotchas |
| `${CLAUDE_SKILL_DIR}/references/protocol.md` | raw HTTP/JSON protocol, error codes, async + streaming + condensed-data mechanics, curl |
| `${CLAUDE_SKILL_DIR}/ra2_mgmt/examples/` | runnable Python examples |
| `${CLAUDE_SKILL_DIR}/../../docs/scripting/device-access.md` | *Which one to use* (RA2 API vs NETCONF per task) and *From a script running in the device* (token mode, `intermediate="localhost"`) — read before writing code that runs in the device |
| https://ripex2-apidocs.racom.eu | official API docs; `ra2 doc` reads them for you (cached) |

## Workflow

1. **Find the method.** `ra2 methods <regex>` (library) or `ra2 doc --search "<words>"` (docs).
   Read `ra2 doc <method> --full` before using a method for the first time — it shows the library
   signature, the exact RPC params/return attributes and the minimal user role.
2. **Read-only first.** `ra2 ping` (no login) → `ra2 call device_info_get_private` → whatever the
   task needs. Reads never need confirmation.
3. **Call it.**
   - `ra2 call <method> key=value…` — typed Device method, parsed `result` as JSON.
   - `ra2 helper <flow> key=value…` — init+reconnect / stream / decode flows (`settings_save`,
     `firmware_upload`, `icmp_ping`, `log_stream`, `monitoring_live`, `statistics`, `status_info`,
     `diagnostic_package`, `reboot`, …). Streams print lines live; bound them (`count`, `max_lines`).
   - `ra2 rpc <method> '{json}'` — raw RPC for the few documented methods the library lacks
     (`radio_freq_tuning_get/set`, `keyring_secret_update_init`) or to see the untouched envelope.
   - `ra2 config get|keys|meta|set` and `ra2 status <types>` — shortcuts for the two most common jobs.
   - Params: one JSON object or `k=v` pairs (values JSON-parsed; `@file` → JSON or base64).
     `--out FILE` saves base64 file results. `--envelope` adds warnings + device timestamp.
4. **More than two dependent calls → Python**, one session:
   ```python
   import sys; sys.path.insert(0, "${CLAUDE_SKILL_DIR}/ra2_mgmt/src")
   from rr_ra2_mgmt.device import Device
   with Device(HOST, USER, PASSWORD, timeout=15, retries=1) as dev:     # HOST may be "ip:port"; or .venv/bin/python after scripts/setup.sh
       cfg = dev.settings_get().result.config_data
   ```
   Every method returns `Envelope(result, warnings, tstamp)`; errors are `Ra2RpcError`
   (`.code`, `.message_id`, `.suberrors[].seids`), `Ra2LoginError`, `Ra2SessionExpiredError`
   (→ `dev.reconnect()`), `Ra2CommunicationError`, all under `Ra2MgmtError`.
5. **Interpret results** using `references/protocol.md` (error codes, seids, condensed data) and
   report `warnings` to the user when present.

## Rules

- **Confirm before any state change.** Methods flagged `[DESTRUCTIVE]` in `ra2 methods` /
  `methods.md` (settings_save*, firmware_*, reboot, reset_*, user_*, keyring_*, file_distribution_*,
  script_start/stop, time_set, …) may restart services, reboot or wipe the device. State exactly
  what will change and wait for an explicit yes. `ra2 config set` is a state change.
- **Configuration is edited, never composed.** `settings_get` → change fields in place → write the
  whole `config_data` back (`ra2 config set` / `helper.settings_save`). Check `ra2 config meta <key>`
  for allowed values. A validation error means nothing was saved — show the `suberrors`.
- **Never guess parameter names or types.** They come from `ra2 doc <method> --full` or
  `ra2 methods <name>`. Values must not contain `" ' \ ; $`.
- **Remote devices**: `--intermediate <reachable-ip> --host <target-ip>` (or `RA2_INTERMEDIATE`);
  credentials are checked on the intermediate; raise `--timeout`.
- **Non-default HTTPS port**: `--port <n>` or `RA2_API_PORT` (default 443). It is the port of the
  device spoken to over HTTPS, so with an intermediate it is the intermediate's port. In Python
  the library has no port argument: pass `"host:port"` as `host` (or as `intermediate`).
- **Timeouts**: the CLI uses 15 s × 1 retry so a dead host fails fast; firmware/reboot flows block
  for the device's own window (minutes) — that is expected, do not kill them.
- **Secrets**: credentials live only in `.env.ra2` / env vars / the user's message. Do not write
  them into files, memory, commits or output; `ra2 env` masks them.
- **Code that runs inside the device (Scripting)** uses `Device(host="localhost",
  token=payload["ra2_api_token"])` — never a password. See patterns.md §13 and the `/racom:scripting` skill.
- **Unknown or odd response?** Re-run with `--envelope` or `ra2 rpc` for the raw JSON, then read
  `references/protocol.md`. Library maintenance (submodule bump): rerun
  `python3 ${CLAUDE_SKILL_DIR}/scripts/gen_catalog.py` and the library tests
  (`bash ${CLAUDE_SKILL_DIR}/scripts/setup.sh` then `.venv/bin/python -m pytest ${CLAUDE_SKILL_DIR}/ra2_mgmt/tests -q`).

$ARGUMENTS
