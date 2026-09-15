# Scripting runtime contract (condensed)

Source: `docs/scripting/writing-scripts.md` (process contract, limits, deployment, debugging) and
`docs/scripting/device-access.md` (interface choice, connection code on the laptop vs inside the device).
This page is the checklist form; the docs win on any disagreement. Section-level pointers are in
SKILL.md → *Documentation map*.

## Vocabulary

| term | meaning |
|------|---------|
| script | one Python source file stored in `rr-sdk-launcher` list `scripts` (key `name`, leaf `source`) |
| instance | `scripts/instances` entry: own arguments and triggers; identified by (script name, instance name) |
| trigger | `scheduler` (cron), `xpath` (config/state change or notification), `manual` (`script_start` RPC / NETCONF action), `system` (reboot, service stop, config removal) |
| execution | one run: id, start time, PID, trigger, `state` (running, terminating, finished, killed), `exit_code`, captured output, `is_outdated` when the source changed since |

## Process contract

1. **stdin** — the launcher writes one JSON object and closes the pipe. Read it first:
   `payload = json.load(sys.stdin)`.

   | key | use |
   |-----|-----|
   | `user` | login name for NETCONF (`user=`) |
   | `user_workdir` | the only writable directory (`/mnt/data/sdk/...`, 10 MB per role) |
   | `ra2_api_token` | `Device(host="localhost", token=...)`; role-bound, valid for this run — **absent on 2.3.6.0** (five keys), **present on 2.3.10.0** (six); use `payload.get()` |
   | `netconf_server_port` | `NetconfDevice(port=...)` |
   | `netconf_server_hostkey` | `hostkey_b64=` (base64 public key of the local server) |
   | `private_key` | `key_base64=payload["private_key"].encode()` per-run SSH key |

   Use `payload.get()` for optional keys; never print, log, or write the payload.
2. **arguments** — instance `arguments` arrive in `sys.argv[1:]`; use `argparse`. The launcher
   validates syntax before starting.
3. **output** — stdout+stderr captured line by line into the execution record.
   `sys.stdout.reconfigure(line_buffering=True)` (or `flush=True`) or a killed run loses output.
   Rate limit 20 KiB/s or 1000 lines/s across all scripts; the loudest script is blocked first
   and gets one marker line `…[log section dropped due to system overload]…`.
4. **stop** — `SIGTERM`, then `SIGKILL` after a timeout → state `killed`. Install a handler, end
   the loop, let `with` blocks close sessions. Sleep in 1 s steps.
5. **limits** (soft/hard): CPU 20 s/∞ (raise: `resource.setrlimit(RLIMIT_CPU, (RLIM_INFINITY,
   RLIM_INFINITY))`), address space 512/1024 MB, data 64/96 MB, file size 1/10 MB, open files
   15/100, processes 20/20 per user, stack 2/4 MB, nice 19.
6. **exit** — 0 success, non-zero failure. `Ra2SessionExpiredError` inside the device is final
   (exit 2 by convention in the templates).
7. **concurrency** — an instance runs at most once at a time; parallel instances are capped;
   `Device`/`NetconfDevice` are not thread-safe.
8. **environment** — Linux user of the creating role, AppArmor default-deny, reduced stdlib plus
   `libyang lxml ncclient ssh sysrepo peewee cffi api2yang sdk_launcher` on 2.3.4.7 and 2.3.6.0.
   **2.3.10.0 adds `rr_ra2_mgmt`, `rr_netconf_mgmt`, `apscheduler`, `tzlocal`** (135 stdlib) — so
   the documented libraries exist from 2.3.10.0 on and not before. On older firmware a script
   needing NETCONF uses `ncclient` + `lxml` with the payload's per-run key. Exact list per
   firmware: `scripting modules --fetch`; `scripting deploy` then refuses a script importing anything the
   target firmware lacks (see *Firmware gate* below).
9. **submodules are not in the list and may be missing** — `logging` is present but
   `logging.handlers` is not (so no `RotatingFileHandler`); on 2.3.6.0 `xml` was present without
   `xml.etree`. The list holds top-level names only; import the submodule in a throwaway script
   before depending on it.
10. **the module list is a path scan, not an import test** — it over-reports. On 2.3.6.0 `sysrepo`,
   `libyang`, `api2yang` and `sdk_launcher` are listed but raise `PermissionError` inside a script,
   and `xml` is listed while `xml.etree` does not exist. Verify anything load-bearing with a
   throwaway script before relying on it.

## Deployment model

Target device for `scripting deploy|run|logs|…`: flags → `RA2_HOST`/`RA2_USER`/`RA2_PASSWORD`
(+ `RA2_API_PORT`, `RA2_NETCONF_PORT`) → `.env.ra2` in the repo root (shared with the api and netconf
skills). Inside the deployed script the device is always `localhost`.

- Prerequisite: `main.RR_Sdk_Enable = On` (`scripting enable --on`, Admin role). Changing the NETCONF
  server settings restarts the Scripting subsystem and every running instance.
- `scripting deploy FILE --instance NAME` → NETCONF `edit-config merge` on
  `<sdk-launcher xmlns="eu:racom:common:rr-sdk-launcher"><scripts><name/><source/><instances><name/>…`.
  Merge replaces the source of a same-named script and leaves other scripts/instances alone.
- Extra instance leaves (`--set LEAF=VALUE`, nested `a/b=VALUE`): names come from
  `netconf tree /rr-sdk-launcher:sdk-launcher` or `references/yang/rr-sdk-launcher.yang`.
- `scripting undeploy NAME` → `edit-config` with `nc:operation="delete"` on the `scripts` entry.
- The same configuration is reachable over the RA2 API as part of `settings_get` /
  `helper.settings_save` (`ra2 config keys` to find the section).
- A script has no version; the launcher hashes the source (`source-version`) and marks older
  executions `is_outdated`.

## Firmware gate

`scripting deploy` asks the target for its firmware version, loads the cached module list for *that*
version and fails when the script imports something absent from it:

```
error L15 [imports] module 'rr_netconf_mgmt' is not importable on firmware 2.3.6.0
scripting: 1 check error(s) against firmware 2.3.6.0; fix them or pass --skip-check
```

With no cached list for that firmware it refuses rather than guessing from another version's list
(`scripting modules --fetch` first). `--firmware VERSION` checks against a cached list without asking the
device; `--skip-check` bypasses the gate entirely.

## The two interfaces disagree on representation

The RA2 API renders `Off`/`On` leaves as int `0`/`1` and enums as integers in **its own** numbering
(2.3.10.0: `Routing_Mode` `0` Static, `1` WWAN (EXT), `2` WWAN (MAIN)); NETCONF uses the YANG
strings, in a different order. Decode with the `config_meta` that comes back with `settings_get`
(`config_meta[section]["children"][TABLE]["columns"][LEAF]["options"]`), never with a hardcoded
index, and write back the shape you read — a save sending `"On"` where the API uses `1` is rejected
and the leaf silently keeps its old value. Full table in `docs/scripting/device-access.md`.

## Run and debug (RA2 API)

| need | RPC | CLI |
|------|-----|-----|
| scripts, instances, last execution | `script_overview_get` | `scripting list` |
| start / stop (manual trigger) | `script_start` / `script_stop` | `scripting start`, `scripting stop`, `scripting run` |
| history | `script_execution_get(pagination, filter={"instances":[{"script_name":…}], "include_outdated":true})` | `scripting executions` |
| output | `script_execution_output_get({"execution_id":ID}, {"limit":1000,"id_exclusive":LAST})` | `scripting logs [--follow]` |

Symptoms: **no output** → buffering or rate limiter; **killed, no exit code** → stop timeout,
SIGXCPU or OOM; **start refused** → already running/terminating, parallel cap, or arguments do
not parse; **`rpc_permission_denied` / `access-denied`** → the instance role is too low.

## Checklist (what `scripting check` verifies)

- [ ] payload read first; never printed/serialised; `sys.stdin` untouched before it
- [ ] line buffering or `flush=True` on every print
- [ ] loops: `SIGTERM` handler, `RLIMIT_CPU` raised, sleeps ≤ 5 s per step
- [ ] no literal passwords/tokens; `Device(... token=...)`, `NetconfDevice(... hostkey_b64=, key_base64=)`
- [ ] no relative imports; only modules present on the firmware
- [ ] files only under `user_workdir`
- [ ] `argparse` for instance arguments
