# Working patterns and recipes

Recipes for the tasks that come up most. CLI first (fast, one-shot), Python second (multi-step,
scripted). All Python assumes the library is importable: either `.venv/bin/python` (after
`scripts/setup.sh`) or `sys.path.insert(0, "<skill>/ra2_mgmt/src")` at the top of a script.
Working examples live in `ra2_mgmt/examples/`.

## 0. Connection resolution

`ra2` and your scripts read, in order: CLI flags → `RA2_*` env vars → `.env.ra2` (walks up from
CWD, then from the skill directory; template `.env.ra2.example`). Check with `ra2 env`.

| var | meaning |
|-----|---------|
| `RA2_HOST` | device IP/hostname (the **target** when `RA2_INTERMEDIATE` is set) |
| `RA2_API_PORT` | HTTPS port of the device API (default 443; `--port`). Applies to the device spoken to over HTTPS: the intermediate when `RA2_INTERMEDIATE` is set, `RA2_HOST` otherwise |
| `RA2_USER` / `RA2_PASSWORD` | credentials for private methods |
| `RA2_TOKEN` | existing session token (skips login/logout) |
| `RA2_INTERMEDIATE` | directly reachable device that forwards to `RA2_HOST` over the radio link |
| `RA2_TIMEOUT` / `RA2_RETRIES` | socket timeout (CLI default 15 s) / retries (CLI default 1; library default 30 s / 3) |

Never echo `RA2_PASSWORD`; `ra2 env` masks it.

The library has no port parameter: it builds `https://<host>/cgi-bin/...`, so in Python pass the
port inside the host string (`Device("10.0.0.5:8443", ...)`; IPv6 as `"[fd00::1]:8443"`). In remote
access put it on the `intermediate=` argument, not on the target `host`.

## 1. Is the device there? What is it?

```bash
ra2 ping                                   # device_ping + device_info_get_public, no credentials
ra2 call device_info_get_private           # name, type, firmware_version, config_version, timezone
ra2 call device_capabilities_get           # RR_DynHw_* flags — what hardware/features exist
ra2 call firmware_status_get               # active / uploaded firmware
```

## 2. One-shot RPC

```bash
ra2 call events_get 'filter={"tstamp_from":0}' limit=20
ra2 call events_get '{"filter": {"severities": ["error","critical"], "id_from_excl": 0}, "limit": 50}'
ra2 call status_info_data_get 'filter={"types":["system_basic"]}'     # raw condensed arrays
ra2 status system_basic ntp radio                                    # decoded records
ra2 call settings_package_get --out backup.zip                       # base64 file → disk
ra2 rpc radio_freq_tuning_get                                        # RPC without a library wrapper
ra2 rpc device_info_get_public --public                              # raw envelope, no login
```

Parameter syntax: one JSON object **or** `key=value` pairs (values JSON-parsed when possible,
`@file` reads a file — text as JSON, binary as base64). `ra2 call` binds by the Python parameter
names shown in `ra2 methods <name>`; `ra2 rpc` sends the dict verbatim as `params`.

Output: `result` as JSON on stdout; `warnings` and device `tstamp` on stderr; `--envelope` for
everything; `--compact` for one line; pipe to `jq` for extraction.

Exit codes: 0 ok · 1 usage · 2 device returned `error` (JSON on stdout) · 3 unreachable · 4 login/session.

## 3. Read and change configuration (the most common write)

The configuration is one document: `config_data` (values), `config_meta` (per-field constraints,
options, labels), `config_tree` (UI layout). Always **read, modify in place, write back the whole
`config_data`** — never build it from scratch, never add keys.

```bash
ra2 config keys                            # sections (main, …)
ra2 config keys main | grep -i station     # keys in a section with previews
ra2 config get main.RR_StationName
ra2 config meta RR_StationName             # constraints / allowed values for a key
ra2 config set main.RR_StationName=Station-7 main.RR_StationDesc='"Roof unit"'   # ← needs user confirmation
```

`config set` = `settings_get` → assign → `helper.settings_save` (init + reconnect; the device may
restart services). It refuses unknown keys and type changes. Output shows `old`, `new`, and
`device` (the value as the device interpreted it — may differ, e.g. trimmed or normalised).

Python:

```python
from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2RpcError

with Device(HOST, USER, PASSWORD) as dev:
    cfg = dev.settings_get().result.config_data
    cfg["main"]["RR_StationName"] = "Station-7"
    try:
        saved = dev.helper.settings_save(cfg).result
    except Ra2RpcError as exc:                       # validation failed: nothing was saved
        for sub in exc.suberrors or []:
            print(sub.seids, sub.message_id, sub.message_params)
        raise
    print(saved.config_data["main"]["RR_StationName"])
```

Tables (`config_data.main.LanInterfaces` etc.) are lists of flat dicts; edit rows in place and
keep every column. Validation errors point at `seids` like `main.LanInterfaces.2.LanIface_Name`.

Backup / restore whole configs: `ra2 call settings_package_get --out cfg.zip` and
`ra2 helper settings_package_save base64=@cfg.zip` (reboots services; confirm first).

## 4. Multi-step flows → Python with one session

Per-call mode (`Device(...).method()`) logs in and out around **every** call. For more than
two calls use one session:

```python
with Device(HOST, USER, PASSWORD, timeout=15, retries=1) as dev:   # login once, logout at exit
    info = dev.device_info_get_private().result
    events = dev.events_get({"tstamp_from": 0, "severities": ["error"]}, 20).result.events
```

`dev.connect()` / `dev.close()` / `dev.reconnect()` when a `with` block does not fit.
On `Ra2SessionExpiredError` call `dev.reconnect()` and retry once. `Device` is not thread-safe.

## 5. Streams (ping, log stream, live monitoring)

```bash
ra2 helper icmp_ping destination_ip=192.168.169.1 size=64 count=5 period=1000 timeout=2000
ra2 helper rss_ping destination_ip=192.168.169.170 count=3 is_go_on_mode=false period=1000 timeout=3000 size=64 traces_reserved=4
ra2 call log_stream_filter_options_get                  # available sources
ra2 helper log_stream 'sources=["system"]' 'severities=["warning","error"]' max_lines=50
ra2 helper monitoring_live max_records=20               # decoded monitoring records as JSON lines
```

Lines print as they arrive (Ctrl-C stops and still sends `*_stop`). Always bound streams with
`count` / `max_lines` / `max_records` when running non-interactively.

## 6. Statistics and status (condensed data, decoded)

```bash
ra2 call statistics_meta_get                                              # available types
ra2 helper statistics 'types=["stat_rlp_ifc"]' tstamp_from=$(date -d '-1 hour' +%s)
ra2 helper statistics_differential 'types=["stat_rlp_ifc"]'
ra2 status system_basic radio neighbors                                   # helper.status_info
```

Records are flat dicts keyed by CSV column title / presentation label; some values are i18n keys
(`ntp.st_val.no_sync`). Translate with `ra2 call messages_get language=en | jq '.<group>'` if needed.

## 7. Diagnostics package

```bash
ra2 helper diagnostic_package configuration=true status=true system_log=true --out diag.zip
```

Takes a while (polls until the device finishes). Flags default to false — opt in per section.
Add `target=<remote-ip>` to collect from a remote unit through this one.

## 8. Firmware, reboot, factory reset (destructive — confirm with the user first)

```bash
ra2 call firmware_status_get
ra2 helper firmware_upload firmware_base64=@ripex2-fw.bin    # upload only; init+reconnect
ra2 helper firmware_activate                                 # device reboots into the new image
ra2 helper reboot
ra2 call reset_factory        # wipes configuration
ra2 call reset_total_purge    # wipes everything incl. logs/keys
```

Expect the CLI to block for the device's own `delay + timeout` window (minutes for firmware).
Increase `--timeout` for large uploads over slow links.

## 9. Remote device over the radio link

```bash
RA2_INTERMEDIATE=192.168.169.169 RA2_HOST=192.168.169.170 ra2 call device_info_get_private
ra2 --intermediate 192.168.169.169 --host 192.168.169.170 --timeout 60 status system_basic
ra2 --intermediate 192.168.169.169 --port 8443 --host 192.168.169.170 ping   # non-default HTTPS port on the intermediate
```

Every request goes to the intermediate with `"target": "<host>"`; credentials must be valid on
the **intermediate**, and Remote Access must be enabled on the target. Use generous timeouts.
`--port` / `RA2_API_PORT` is the intermediate's HTTPS port; the target is addressed by IP only.
`remote_call_error` = target unreachable or Remote Access disabled.

## 10. Users, keyring, scripts

```bash
ra2 call users_get ; ra2 call user_attributes_get
ra2 call user_create username=tech1 password=... role=tech          # destructive
ra2 call keyring_status_get ; ra2 call keyring_secret_status_get id=...
ra2 call script_overview_get ; ra2 call script_execution_get 'filter={}' 'pagination={"limit":20}'
```

Check `ra2 doc <method> --full` for exact parameter names and roles before calling anything you
have not used before — argument lists for these differ per firmware generation.

## 11. Looking things up

```bash
ra2 methods radio                  # library methods matching a regex (name or summary)
ra2 doc settings_save_init         # library signature+docstring, then the API docs summary
ra2 doc event-filter --full        # full model page with the attribute table
ra2 doc --search "hot standby"     # full-text search over the docs index
ra2 doc --list Models              # every model page
```

Docs are cached for 7 days under `~/.cache/ra2-apidocs/` (`--refresh` to force). If the docs
site is unreachable the library docstrings (`ra2 doc <name>`, `references/methods.md`) still work.

## 12. Gotchas

- Self-signed TLS everywhere — the library disables verification; use `curl -k`.
- A TCP-reachable host whose TLS handshake hangs is not a RipEx2 (or is firewalled). `ra2 ping`
  with `--timeout 5` tells quickly.
- `settings_save_init` returns HTTP 200 with `error` **and** `result` on validation failure: read
  `suberrors`, nothing was applied.
- After `firmware_activate` / `reboot` the session is gone; the Helper flows handle it, your own
  code must `reconnect()`.
- Statistics `tstamp_*` are Unix seconds in **device** time (`Envelope.tstamp` tells you the
  device clock); `time_set` fixes the clock.
- Attribute values must not contain `" ' \ ; $`.
- `Device` default timeout/retries (30 s × 3) make an unreachable host fail after ~100 s; the CLI
  defaults to 15 s × 1. Raise them for remote (`intermediate`) targets.
- Public methods: `device_ping`, `device_info_get_public`, `messages_get` — everything else needs
  a role of at least Guest; each docs page names the minimal role.

## 13. From inside a script (code that runs in the device)

The Scripting launcher (`/racom:scripting`, `docs/scripting/`) starts scripts inside the device and hands them a
JSON payload on stdin. There is no password: use **token mode** and `localhost`.

```python
import json, sys
payload = json.load(sys.stdin)                                   # first thing, never print it
from rr_ra2_mgmt.device import Device
with Device(host="localhost", token=payload["ra2_api_token"]) as dev:        # no login/logout happens
    info = dev.device_info_get_private().result
remote = Device(host="10.0.0.7", token=payload["ra2_api_token"], intermediate="localhost")  # other unit via radio
```

- With `token=`, `connect()`/`close()` are no-ops and `Ra2SessionExpiredError` is **not**
  recoverable — log it and exit non-zero; the next run gets a fresh token.
- The token carries the role of the instance; `rpc_permission_denied` means the script's role
  is too low, not a bug.
- `CLI equivalent`: `ra2 --host <device> --token <token> call ...` (`RA2_TOKEN`).
