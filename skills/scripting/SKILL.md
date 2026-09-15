---
name: scripting
description: Write, check, deploy and run Python scripts that execute INSIDE a RACOM RipEx2 / RA2 device under the Scripting launcher (rr-sdk-launcher). Use whenever the user mentions Scripting, device scripts, script instances, triggers (scheduler/xpath/manual), the launcher payload, ra2_api_token, user_workdir, script_start/script_stop, execution logs, or wants automation running on the device itself. Provides the `scripting` CLI (new, check, modules, enable, deploy, undeploy, list, start, stop, run, logs, executions), script templates and the runtime contract.
argument-hint: "[script.py | task]"
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/scripting env*), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting new*), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting check*), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting modules), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting list*), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting logs*), Bash(${CLAUDE_SKILL_DIR}/scripts/scripting executions*)
---

# Scripting for RipEx2 / RA2

A script is **one Python file stored in the device configuration**; the `sdk_launcher`
daemon runs it as a confined process when a trigger fires and hands it credentials on stdin.
Code that runs there is different from code on a laptop: no passwords, `localhost`, hard
resource limits, output captured into an execution log. This skill exists so those differences
are not learned by trial and error on a live radio.

**Current connection** (`scripting env`; password masked):

!`${CLAUDE_SKILL_DIR}/scripts/scripting --compact env 2>&1`

**How the target device is chosen.** Every `scripting` command that talks to a device (`enable`, `deploy`,
`undeploy`, `list`, `start`, `stop`, `run`, `logs`, `executions`, `modules --fetch`) resolves the
connection in this order: CLI flags (`--host --user --password --netconf-port --timeout`) →
environment variables `RA2_HOST`, `RA2_USER`, `RA2_PASSWORD`, `RA2_API_PORT`, `RA2_NETCONF_PORT`,
`RA2_TIMEOUT` → the `.env.ra2` file found by walking up from the current directory (repo root,
git-ignored, template `.env.ra2.example`). It is the same file the `/racom:api` and
`/racom:netconf` skills use, so one device configuration serves all three. If `host` above is
`null`, ask the user for the device address and credentials or point them to the template; never
put credentials on the command line in shared output. This target is where scripts are
**deployed to and run on** from the laptop — the script itself always talks to `localhost`.

## Tools

| | |
|---|---|
| `${CLAUDE_SKILL_DIR}/scripts/scripting` | CLI. RA2-API commands run on system python3; `deploy`/`undeploy`/`modules --fetch` need `<repo>/.venv` (`bash skills/netconf/scripts/setup.sh`) |
| `${CLAUDE_SKILL_DIR}/templates/` | `oneshot.py`, `continuous.py`, `*_netconf.py`: skeletons that already satisfy the contract (`scripting new` fills them in) |
| `${CLAUDE_SKILL_DIR}/references/runtime.md` | the contract in one page: payload keys, limits, signals, output, deployment model, checklist |
| `${CLAUDE_SKILL_DIR}/references/modules/` | cached list of modules importable per firmware (`scripting modules`, refresh with `--fetch`) |
| `${CLAUDE_SKILL_DIR}/references/yang/` | `rr-sdk-launcher.yang` once dumped from a device (`netconf schema rr-sdk-launcher --out …`) |
| `${CLAUDE_SKILL_DIR}/../../docs/scripting/` | the authoritative Scripting documentation — see **Documentation map** below; read the named section before acting, do not work from memory |
| `/racom:api`, `/racom:netconf` | the device-side libraries the script uses: `rr_ra2_mgmt` (token mode) and `rr_netconf_mgmt` (key login) |

## Documentation map (`${CLAUDE_SKILL_DIR}/../../docs/scripting/`)

The documents are the source of truth; `references/runtime.md` is only their checklist form.
Open the section that matches the step you are on:

| when you are… | read | what it settles |
|---------------|------|-----------------|
| deciding RA2 API vs NETCONF for a task | `device-access.md` → *The two interfaces* / *Which one to use* | the task→interface table; NETCONF is for configuration edits, the RA2 API for everything else |
| writing connection code **inside a script** | `device-access.md` → *From a script running in the device* (RA2 API / NETCONF subsections, *Connection parameters at a glance*) | token mode, `intermediate="localhost"`, unrecoverable session expiry, `hostkey_b64` + `key_base64`, NACM, why passwords are discouraged |
| writing connection code **on the laptop** (deploy tools, tests) | `device-access.md` → *From a local computer* | `.env.ra2`, context manager use, `hostkey_verify=False`, XPath filtering, merge vs replace |
| starting a script | `writing-scripts.md` → *Recommended script skeleton*, *Vocabulary* | canonical structure; script / instance / trigger / execution terms |
| handling stdin, arguments, output, exit codes | `writing-scripts.md` → *Process contract* (*The stdin payload*, *Output*, *Exit code and state*) | payload keys and secrecy, line buffering, the 20 KiB/s rate limiter and its marker line, `killed` semantics |
| writing a loop or anything longer than seconds | `writing-scripts.md` → *Signals and graceful stop*, *Resource limits*, *Concurrency* | SIGTERM→SIGKILL, `RLIMIT_CPU` 20 s, memory/file/process limits, one run per instance |
| choosing imports | `writing-scripts.md` → *Runtime environment* + `examples/list_modules.py` | reduced stdlib, the third-party set, verify per firmware |
| deploying | `writing-scripts.md` → *Deploying a script* | the `rr-sdk-launcher` XML shape, RA2-API alternative, `RR_Sdk_Enable`, role requirements, `source-version`/`is_outdated` |
| a run misbehaves | `writing-scripts.md` → *Running and debugging* | which RPC shows what; missing output, `killed`, start refused |
| about to deploy | `writing-scripts.md` → *Checklist before deploying* | the same list `scripting check` enforces |
| looking for working code | `examples/README.md` → table of the 8 in-device scripts and 2 local tools | `script_context.py` (template), `ra2_status_monitor.py` (continuous), `netconf_poll_reconnect.py` (reconnect), `local/deploy_script.py`, `local/run_and_fetch_output.py` |

`README.md` there is the index with a four-step quick start. When a doc and this skill disagree,
the doc wins — then fix the skill (`references/runtime.md`, templates, `scripting check` rules).

## Workflow

1. **Start from a template, never from scratch**: `scripting new NAME [--kind continuous|oneshot] [--netconf]`.
   The skeleton already reads the payload first, line-buffers output, parses `argparse`
   arguments, raises `RLIMIT_CPU` and handles `SIGTERM` when it loops.
2. **Write the work part.** Read `device-access.md` → *From a script running in the device*
   first (Documentation map). Device access from inside the script:
   - RA2 API: `Device(host="localhost", token=payload["ra2_api_token"])`; other unit over the
     radio: `Device(host=IP, token=..., intermediate="localhost")`. `Ra2SessionExpiredError` is
     final here — exit non-zero, the next run gets a new token.
   - NETCONF: `NetconfDevice(host="localhost", port=payload["netconf_server_port"],
     user=payload["user"], hostkey_b64=payload["netconf_server_hostkey"],
     key_base64=payload["private_key"].encode())`. Never `hostkey_verify=False` here.
   - Find RPC methods with `ra2 methods|doc`, YANG paths with `netconf find` (both skills).
3. **Check before every deploy**: `scripting check script.py`. Errors (secrets, missing SIGTERM /
   RLIMIT_CPU in loops, stdin misuse, relative imports, unknown modules when a module list is
   cached) block `scripting deploy`; warnings are judgement calls — read them.
4. **Deploy and run** (state changes — confirm with the user first; `writing-scripts.md` → *Deploying a script*):
   `scripting enable` (must be `On`), `scripting deploy script.py --instance NAME [--set LEAF=VALUE]`,
   `scripting run script.py NAME --follow`, then `scripting logs`, `scripting executions`, `scripting list`.
   Instance leaves other than `name` (arguments, triggers) must be looked up first:
   `netconf tree /rr-sdk-launcher:sdk-launcher` — do not guess their names.
5. **Iterate** (`writing-scripts.md` → *Running and debugging* when a run misbehaves): edit → `scripting check` → `scripting deploy` (merge replaces the source, keeps instances)
   → `scripting run`. `scripting undeploy NAME` removes script and instances. Refresh the importable-module
   list once per firmware with `scripting modules --fetch` (deploys a helper script).

## Rules

- **Read the payload first, never print or store it.** It holds `ra2_api_token`, `private_key`,
  `netconf_server_hostkey`. Print presence or length at most.
- **No passwords in script source.** The source is stored in the configuration and visible to
  every admin. Token (RA2 API) or per-run key (NETCONF) only.
- **Every loop needs `SIGTERM` handling and a raised `RLIMIT_CPU`**, sleeps in 1 s steps. Without
  them a stop ends in `SIGKILL` (state `killed`) and CPU time over 20 s ends in `SIGXCPU`.
- **Output is a captured, rate-limited pipe** (20 KiB/s, 1000 lines/s across all scripts): line
  buffering on, one summary line per iteration, never per record.
- **One file, reduced stdlib.** No second module, no `pip`. `scripting modules` lists what imports;
  `lxml`, `ncclient` and `ssh` import on every firmware measured. **`rr_ra2_mgmt` and
  `rr_netconf_mgmt` exist from 2.3.10.0 on** (with `ra2_api_token` in the payload); 2.3.4.7 and
  2.3.6.0 have neither, so in-device NETCONF there means raw `ncclient` + `lxml`. `sysrepo`,
  `libyang`, `api2yang` and `sdk_launcher` are listed but `PermissionError` inside a script.
  `scripting deploy` gates on the target firmware's module list; verify anything else with a throwaway
  script.
- **Files only under `payload["user_workdir"]`** (10 MB per role); AppArmor denies the rest.
- **Deploying, starting, stopping, enabling Scripting are device state changes.** Say what will be
  written (script name, instance, arguments) and wait for a yes. `scripting deploy --dry-run` shows
  the exact XML. Prefer `merge` semantics (default); `undeploy` deletes.
- **Exit codes mean something**: 0 success, non-zero failure; the execution record shows
  `state`/`exit_code`. `killed` without exit code = SIGKILL (stop timeout, SIGXCPU, OOM).
- **Local tools are not device scripts.** Anything reading `RA2_HOST`/`.env.ra2` runs on the laptop
  (`scripting check` says so and skips the contract).

$ARGUMENTS
