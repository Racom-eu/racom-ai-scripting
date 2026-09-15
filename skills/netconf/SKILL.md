---
name: netconf
description: Manage RACOM RipEx2 / RA2 radio devices over NETCONF (RFC 6241, SSH port 830) with YANG-modelled configuration. Use whenever the user mentions NETCONF, YANG, get-config/edit-config, XPath filters, the mbm-root model, libyang, ncclient, yanglint, schema/module lists, or wants device configuration read or changed through NETCONF instead of the HTTPS API. Provides the `netconf` CLI (browse the YANG tree, find a leaf's path, get/set leaves, edit-config from files, operational <get>, raw RPC) and the rr_netconf_mgmt Python library.
argument-hint: "[path-or-task]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/netconf env*), Bash(${CLAUDE_SKILL_DIR}/scripts/netconf methods*), Bash(${CLAUDE_SKILL_DIR}/scripts/netconf doc*), Bash(${CLAUDE_SKILL_DIR}/scripts/netconf find*), Bash(${CLAUDE_SKILL_DIR}/scripts/netconf tree*)
---

# RipEx2 / RA2 over NETCONF

Same devices and credentials as the HTTPS-API skill (`/racom:api`), different protocol: SSH,
XML, YANG. Use this skill when the user wants NETCONF specifically, or when a YANG-typed,
schema-validated edit is preferable. Look paths up in the schema instead of guessing.

**Current connection** (`netconf env`; password masked):

!`${CLAUDE_SKILL_DIR}/scripts/netconf --compact env 2>&1 || echo '{"error":"environment not set up — run bash ${CLAUDE_SKILL_DIR}/scripts/setup.sh"}'`

If `host` is `null`, ask for the device address/credentials or point to `.env.ra2.example` →
`.env.ra2` (repo root, git-ignored, shared with `/racom:api`). If the line reports an error, run
`bash ${CLAUDE_SKILL_DIR}/scripts/setup.sh` first (builds libyang C 4.2.2, creates `.venv`).

## Tools

| | |
|---|---|
| `${CLAUDE_SKILL_DIR}/scripts/netconf` | CLI (wrapper picks `<repo>/.venv/bin/python`). Alias: `alias netconf=${CLAUDE_SKILL_DIR}/scripts/netconf` |
| `${CLAUDE_SKILL_DIR}/rac_netconf_mgmt/` | `rr_netconf_mgmt` (git submodule): `NetconfDevice` (session, get/edit-config, get-schema) + `NetconfConfig` (XML + YANG → dict, `get`/`set` by data path) |
| `${CLAUDE_SKILL_DIR}/references/methods.md` | generated catalog of the library API |
| `${CLAUDE_SKILL_DIR}/references/patterns.md` | recipes: find a path, read, set, edit from file, operational data, sessions, gotchas |
| `${CLAUDE_SKILL_DIR}/references/protocol.md` | NETCONF operations used, error tags, the mbm-root model, the three path formats, raw RPC |
| `${CLAUDE_SKILL_DIR}/rac_netconf_mgmt/examples/` | runnable Python examples + `mbm-root@2023-04-29.yang` sample model |
| `${CLAUDE_SKILL_DIR}/../../docs/scripting/device-access.md` | *Which one to use* (NETCONF = validated configuration edits) and *NETCONF from inside the device* (`hostkey_b64`, `key_base64`, NACM) — read before writing code that runs in the device |
| `yanglint` (`/usr/local/bin`) | offline YANG validation / tree printing |

## Workflow

1. **Check the device speaks NETCONF**: `netconf ping` (session id, capabilities, module count).
   `netconf modules` lists YANG modules; the root model is `mbm-root`.
2. **Find the path.** `netconf find <name>` returns the libyang data path, type, enum values,
   default and description of matching leaves (`--desc` searches descriptions, `--offline` uses
   the bundled model when no device is reachable). `netconf tree /mbm-root:config_data/main/Main`
   shows a whole settings page. Leaf names equal the HTTPS-API `config_data` keys (`RR_*`).
3. **Read**: `netconf get <xpath>` (typed JSON) / `--xml`; `netconf leaf <path>` for one value;
   `netconf state <xpath> [--json]` for operational data (`/mbm-root:Diagnostics`).
   Always filter with an XPath — the unfiltered config downloads every schema.
4. **Write** (after confirmation): `netconf set <path>=<value> …` (parent subtree fetched, leaf
   set with libyang validation, `edit-config merge`, re-read and reported), or
   `netconf edit file.xml|file.json [--operation merge|replace]`.
5. **Anything else**: `netconf rpc '<xml/>'` dispatches a raw RPC and prints the reply;
   `netconf schema <module> --out f.yang` dumps YANG for `yanglint`.
6. **Multi-step or lock/edit/unlock sequences → Python**, one session:
   ```python
   # run with <repo>/.venv/bin/python
   from rr_netconf_mgmt.device import NetconfDevice
   with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as dev:
       cfg = dev.get_config(xpath="/mbm-root:config_data/main/Main")
       cfg.set("/mbm-root:config_data/main/Main/RR_StationName", "Unit-7")
       dev.set_config(cfg)                      # edit-config merge into running
   ```
   Errors: `NetconfMgmtConnectionError` → `NetconfMgmtSessionClosedError` (call `reconnect()`),
   `NetconfMgmtConfigError` (edit rejected, bad path, unknown prefix), `NetconfMgmtOperationError`.

## Rules

- **Confirm before writing.** `netconf set`, `netconf edit`, `netconf rpc` with edit/copy/delete
  operations and `NetconfDevice.set_config` change the running configuration immediately; some
  changes restart services or the radio. State the exact leaves and values, wait for a yes.
- **`merge` by default; `replace` only on request** and only with a complete subtree — omitted
  leaves are reset to defaults.
- **Use the three path formats correctly** (protocol.md): data path
  `/mbm-root:config_data/main/Main/RR_StationName` for `leaf/set/find/tree`; the same prefix style
  for XPath filters; module-prefixed top-level key `"mbm-root:config_data"` in JSON. The prefix is
  the module *name* (`mbm-root`), never the YANG prefix (`rr`).
- **Validate against the schema first.** `netconf find` gives type/enum/default; the CLI's `set`
  fails locally on an invalid value before sending. Report `rpc-error` `error-tag`/`error-path`
  to the user verbatim when the device rejects an edit.
- **Secrets** live in `.env.ra2` / env vars / the user's message only. `netconf env` masks them;
  never echo them elsewhere. Host-key verification is off by default because units have
  individual keys — say so if the user asks about security.
- **Code that runs inside the device (Scripting)** logs in with the launcher's key:
  `NetconfDevice(host="localhost", port=payload["netconf_server_port"], user=payload["user"],
  hostkey_b64=payload["netconf_server_hostkey"], key_base64=payload["private_key"].encode())`.
  The Scripting configuration itself is the `rr-sdk-launcher` module (`netconf tree /rr-sdk-launcher:sdk-launcher`);
  use the `/racom:scripting` skill to deploy and run scripts.
- **Environment**: the library needs libyang C ≥ 4.2.2 and the `.venv`; `scripts/setup.sh` is
  idempotent and safe to rerun. After a submodule bump run
  `.venv/bin/python ${CLAUDE_SKILL_DIR}/scripts/gen_catalog.py` and
  `.venv/bin/python -m pytest ${CLAUDE_SKILL_DIR}/rac_netconf_mgmt/tests -q`.
- **Cross-check with the HTTPS API** when in doubt: both protocols edit the same configuration;
  `/racom:api` (`ra2 config get main.RR_StationName`) confirms a NETCONF change.

$ARGUMENTS
