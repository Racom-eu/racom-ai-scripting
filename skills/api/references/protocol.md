# Racom-RPC wire protocol (RipEx2 / RA2)

What actually goes over HTTPS. Read this when you need `curl`, when the library has no wrapper
for an RPC (`ra2 rpc`), or when a response looks odd. Source of truth: the API docs
(`ra2 doc racom-rpc --full`, `ra2 doc authentication --full`) and `ra2_mgmt/src/rr_ra2_mgmt/transport.py`.

## Endpoints

All are `POST https://<host>/cgi-bin/<script>` with `Content-Type: application/json`. The HTTPS port
is 443 unless the device is configured otherwise; then `<host>` is `ip:port` (`ra2 --port` /
`RA2_API_PORT`, e.g. `H=https://192.168.169.169:8443/cgi-bin` in the curl examples below).
Devices use self-signed certificates: always `curl -k` / `ssl._create_unverified_context()`.

| Script            | Purpose                                            | Auth header |
|-------------------|----------------------------------------------------|-------------|
| `login.cgi`       | create a session → `{"token": ..., "role": {...}}` | none        |
| `logout.cgi`      | destroy the session (body `{}`)                    | `apikey`    |
| `rpc.cgi`         | every RPC method                                   | `apikey` (omit for public methods) |
| `first_login.cgi` | create the first admin account when the device has none (`device_info_get_public` → `first_login_required`) | none |

`login.cgi` body: `{"username": "...", "password": "...", "language_code": "en"}`.
HTTP status is the only success signal: 200 ok · 400 wrong credentials · 500 server error ·
503 temporarily unavailable (also the lockout after failed attempts).
Sessions expire after `device_constants_get().web_session_inact_timeout` seconds of inactivity;
an expired token makes `rpc.cgi` answer **HTTP 401** (library: `Ra2SessionExpiredError`).

## Request

```json
{ "method": "events_get",
  "params": { "filter": { "tstamp_from": 0 }, "limit": 5 },
  "language_code": "en",
  "target": "10.10.10.170" }
```

- `params` is optional (omit for parameterless methods).
- `target` = IP of a **remote** device; the addressed device forwards the call over the radio
  network (Remote Access must be enabled on the target). Library: `Device(target, ..., intermediate=host)`.
- Attribute values must never contain the characters `" ' \ ; $`.

## Response

Two formats, both sharing `error`, `warnings`, `device.tstamp`:

```json
{ "result": { ... },                                   // standard format
  "error":   { "code": "...", "message_id": "...", "message_params": {...}, "log": "...", "suberrors": [...] },
  "warnings": [ { "code": "...", "message_id": "...", "message_params": {...}, "subwarnings": [...] } ],
  "device": { "tstamp": 1741858385 } }

{ "base64": "UEsDB...", "extension": "zip", "device": { "tstamp": ... } }   // root format (file downloads)
```

Root-format methods (payload beside, not under, `result`): `settings_package_get`,
`diagnostic_package_file_get`, `users_backup_get`, `keyring_backup_get`, `keyring_secret_download`,
`keyring_key_csr_download`, `users_get`. The library hides this (`result_at_top_level=True`);
with `ra2 rpc` you see the raw shape.

`error` and `result` can both be present (e.g. `settings_save_init` on a validation error returns
the validated `config_data` alongside the error). The library raises `Ra2RpcError` and keeps the
parsed result in `exc.result`.

### Error codes (Models/error)

| code | meaning |
|------|---------|
| `rpc_invalid_request` / `rpc_invalid_parameter` | payload or params malformed — check `ra2 doc <method> --full` |
| `rpc_method_not_found` | wrong method name or firmware too old |
| `rpc_permission_denied` | user role too low for this method (docs pages show the minimal role: Guest/Tech/Admin) |
| `validation_error` / `settings_save_error` | config rejected — details in `suberrors[].seids` + `message_id` |
| `rpc_async_action_in_progress` | keep polling the `*_reconnect` method |
| `rpc_temporarily_unavailable` | device busy / mid-reboot — retry later (library keeps polling) |
| `async_action_end_error` / `async_action_end_minor` / `async_action_lost` | async job finished badly or was lost |
| `remote_call_error` | `target` device unreachable or Remote Access disabled |
| `firmware_upgrade_error`, `user_*_invalid`, `users_restore_invalid`, `self_passwd_invalid` | operation-specific failures |
| `rpc_server_error`, `rpc_compatibility_error`, `rpc_fatal_error` | server side; nothing to fix in the request |

`seid` (in `suberrors[].seids`) addresses one configuration entity:
`group.name[.row[.col]]`, e.g. `main.RR_StationName`, `main.LanInterfaces.2.LanIface_Name`,
`main.LanInterfaces..LanIface_Name` (whole column).

## Asynchronous methods (init + reconnect)

Anything that restarts a daemon or reboots: `settings_save`, `settings_package_save`, `reboot`,
`firmware_upload`, `firmware_activate`, `firmware_module_activate`, `tamper_reset`,
`keyring_backup_restore`, `keyring_secret_generate`, `keyring_secret_upload`, `keyring_secret_update`.

1. `<op>_init` (params of the operation) → `{delay, interval, interval_increase, timeout, session_id}`.
2. Wait `delay` s. `timeout` counts from the moment the init response arrived.
3. Loop until `timeout`: call `<op>_reconnect {"session_id": ...}`.
   - `rpc_async_action_in_progress` / `rpc_temporarily_unavailable` / connection error → sleep
     `interval`, then `interval += interval_increase`, retry.
   - anything else → final result (or final error).

Library: `device.helper.settings_save(...)`, `helper.reboot()`, `helper.firmware_upload(...)`, … or
`Device._run_init_reconnect(init, reconnect, params, ResultType)` for a new pair.
The intervals are tuned per operation and transport (local vs. remote): respect them.

## Streaming methods (start / poll / stop)

`icmp_ping`, `rss_ping`, `log_stream` (and the Helper's `monitoring_live` uses the same cadence).

1. `<op>_start` (operation params) → Poll Init: `{process_id, min_interval, max_interval,
   min_interval_threshold, max_interval_threshold}`.
2. Poll `<op>_poll {"process_id": ...}` → `{lines: [...], is_last | omitted}`; interval starts at
   `min_interval`, ramps linearly to `max_interval` between the two thresholds (seconds since start).
3. `<op>_stop {"process_id": ...}` — always, also on abort (library does it in `finally`).

## Diagnostic package (start / status / file)

`diagnostic_package_start` → `process_id`; poll `diagnostic_package_status_get` until
`return_code` is `success` (or `error`); then `diagnostic_package_file_get` (root format, base64
ZIP). Library: `helper.diagnostic_package(configuration=True, status=True, ...)`.

## Condensed data

Status, statistics, monitoring and overview data are shipped as **nested arrays without keys**.
A template tells you where each value sits via a **cdid** pointer `.a.b.c` (numeric indexes into
the nesting; an empty segment `.0..1` maps over every element of an array). Trailing elements may be
omitted (implicit defaults).

| data RPC | template RPC | template kind | library decoder |
|----------|--------------|---------------|-----------------|
| `status_info_data_get {filter:{types:[…]}}` | `status_info_schema_get` / `status_info_presentation_get` | Condensed Schema / Presentation | `helper.status_info(types)` |
| `statistics_data_get {filter}` / `statistics_differential_data_get` | `statistics_csv_get` (also `_presentation_get`) | Condensed CSV | `helper.statistics(types, tstamp_from=…)` |
| `monitoring_live_data_get {id_from_excl}` / `monitoring_log_data_get` | `monitoring_schema_get` | Condensed Schema | `helper.monitoring_live()` / `helper.monitoring_log()` |
| `status_overview_data_get` / `alarm_overview_data_get` | `status_overview_card_get` / `alarm_overview_card_get` | Condensed Card | none (raw) — `rr_ra2_mgmt.condensed._resolve_cdid` helps |

Valid `types` for status info are listed in `ra2 doc status-info-filter --full`
(`system_basic`, `radio`, `ntp`, `eth_port_1`, `neighbors`, `routing_system`, …); statistics types
come from `statistics_meta_get`. Decoded labels/values may still be i18n keys
(`system_basic.prod_code`) — translate with `messages_get("en")` if you need human text.

## curl cheat-sheet

```bash
H=https://192.168.169.169/cgi-bin
# public, no login
curl -sk -X POST -H 'Content-Type: application/json' -d '{"method":"device_info_get_public"}' $H/rpc.cgi
# login → token
TOKEN=$(curl -sk -X POST -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"***","language_code":"en"}' $H/login.cgi | jq -r .token)
# private call
curl -sk -X POST -H 'Content-Type: application/json' -H "apikey: $TOKEN" \
     -d '{"method":"settings_get"}' $H/rpc.cgi | jq '.result.config_data.main.RR_StationName'
# remote device through this one
curl -sk -X POST -H 'Content-Type: application/json' -H "apikey: $TOKEN" \
     -d '{"method":"device_info_get_private","target":"192.168.169.170"}' $H/rpc.cgi
# logout
curl -sk -X POST -H 'Content-Type: application/json' -H "apikey: $TOKEN" -d '{}' $H/logout.cgi
```

`ra2 rpc <method> '<params-json>'` does the login/call/logout dance for you and prints the raw envelope.
