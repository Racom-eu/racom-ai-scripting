# Accessing the device: RA2 API and NETCONF

A RipEx2 / RA2 device has two management interfaces. This document explains what each one is
for and how the connection is set up in the two places code can run: on a **local computer**
(developer workstation, NMS, CI) and **inside the device** as a script started by the Scripting
launcher.

## The two interfaces

### RA2 API (Racom-RPC over HTTPS)

- JSON-RPC style calls over HTTPS (`POST /cgi-bin/rpc.cgi`; sessions via `login.cgi` / `logout.cgi`),
  self-signed TLS, one method per operation.
- Authentication: user name + password (`login` returns a session token) or an existing token
  passed in the `apikey` header. Public methods (`device_ping`, `device_info_get_public`,
  `messages_get`) need no login.
- Covers the whole device: information, status and statistics (condensed data), events,
  configuration (`settings_get`, `settings_save_*`), users, keyring, firmware, diagnostics,
  streams (ping, log stream, live monitoring) and Scripting itself (`script_overview_get`,
  `script_start`, `script_stop`, `script_execution_get`, `script_execution_output_get`,
  `script_execution_filter_options_get`).
- Python client: `rr_ra2_mgmt` (`Device`, `device.helper`, `Envelope`). Method names and
  parameters follow <https://ripex2-apidocs.racom.eu>; look a method up there before first use.
- Interactive use from this repository: `skills/api/scripts/ra2` (`ra2 doc <method> --full`,
  `ra2 call <method> k=v`, `ra2 config get|set`, `ra2 helper <flow>`).

### NETCONF (RFC 6241 over SSH)

- SSH subsystem `netconf` on port 830, XML messages, data modelled in YANG.
- Root module of the device configuration is `mbm-root` (namespace `eu:racom:mbm:root`); the
  Scripting configuration lives in `rr-sdk-launcher` (namespace `eu:racom:common:rr-sdk-launcher`).
  Leaf names under `mbm-root:config_data` are the same `RR_*` identifiers the RA2 API uses in
  `config_data`, only wrapped in one more container per settings page:
  `config_data.main.RR_StationName` (RA2 API) is
  `/mbm-root:config_data/main/Main/RR_StationName` (NETCONF).
- Used for **configuration updates only**: `get-config` and `edit-config` on the `running`
  datastore. Every edit is validated against the YANG schema before it is applied, and an
  edit can touch a single leaf without sending the whole configuration document. Operational
  data and actions exist in the YANG model, but the supported pattern is NETCONF for
  configuration and the RA2 API for everything else.
- Python client: `rr_netconf_mgmt` (`NetconfDevice`, `NetconfConfig`).
- Interactive use from this repository: `skills/netconf/scripts/netconf` (`netconf find`,
  `netconf tree`, `netconf get`, `netconf set`, `netconf edit`, `netconf schema`).

### Which one to use

| task | interface |
|------|-----------|
| read device info, status, statistics, events | RA2 API |
| change one or a few configuration leaves, validated against the schema | NETCONF (`edit-config`, `merge`) |
| change configuration as a whole document (read, modify in place, write back) | RA2 API (`settings_get` + `helper.settings_save`) |
| install or update scripts and instances, set their triggers | NETCONF on `rr-sdk-launcher` (also reachable through `settings_get` / `settings_save_*` as part of the device configuration) |
| start or stop a script instance, read execution logs and output | RA2 API (`script_*` methods) |
| firmware, reboot, diagnostics, ping and log streams | RA2 API |
| reach another device over the radio link | RA2 API with `intermediate=` |

Both interfaces edit the **same** configuration. A change made over NETCONF shows up in
`settings_get` and vice versa. Some configuration changes restart services or the radio; the
reply arrives before that happens, so re-read after a moment when verifying.

## From a local computer

Both interfaces are reached over the network with the device's user accounts. The same user name
and password work for HTTPS and NETCONF. The factory default address of a RipEx2 is
`192.168.169.169`.

Keep credentials out of source code. In this repository they live in `.env.ra2` (git-ignored,
template `.env.ra2.example`) or in `RA2_*` environment variables; both CLIs and the examples in
`examples/local/` read them from there. Ports have their own variables: `RA2_API_PORT` (HTTPS,
default 443) for the RA2 API and `RA2_NETCONF_PORT` (SSH, default 830) for NETCONF.

### RA2 API from a local computer

```python
import os

from rr_ra2_mgmt.device import Device

host = os.environ["RA2_HOST"]
port = os.environ.get("RA2_API_PORT", "443")
if port != "443":
    host = f"{host}:{port}"                            # the library has no port argument

with Device(host, os.environ["RA2_USER"], os.environ["RA2_PASSWORD"],
            timeout=15, retries=1) as device:          # login once, logout at exit
    info = device.device_info_get_private().result
    print(info.name, info.firmware_version)
```

- Use `Device` as a context manager (or `connect()` / `close()`) for more than one call; calling
  a method on an unconnected `Device` logs in and out around every call.
- The API is `https://<host>/cgi-bin/...`; a non-default port travels inside the host string
  (`"10.0.0.5:8443"`, IPv6 `"[fd00::1]:8443"`). The `ra2` CLI does this for `--port` / `RA2_API_PORT`.
- `Ra2SessionExpiredError` means the token died (HTTP 401): call `device.reconnect()` and retry.
- Remote device over the radio link: `Device(host=TARGET, ..., intermediate=REACHABLE_DEVICE)`.
  Credentials are checked on the intermediate; Remote Access must be enabled on the target. A
  non-default port belongs on `intermediate` (the HTTPS peer), never on the target `host`.

### NETCONF from a local computer

```python
import os

from rr_netconf_mgmt.device import NetconfDevice

with NetconfDevice(host=os.environ["RA2_HOST"], user=os.environ["RA2_USER"],
                   password=os.environ["RA2_PASSWORD"], hostkey_verify=False) as device:
    config = device.get_config(xpath="/mbm-root:config_data/main/Main")
    config.set("/mbm-root:config_data/main/Main/RR_StationName", "Unit-7")
    device.set_config(config)                            # edit-config, default-operation merge
```

- Devices have per-unit SSH host keys, so either disable verification (`hostkey_verify=False`),
  keep the key in `known_hosts`, or pass the expected key with `hostkey_b64=`.
- Always filter `get_config` with an XPath. A full `get_config()` downloads every YANG module,
  which is slow over a radio link.
- `set_config(config, operation="replace")` resets everything you omit in the addressed subtree.
  Stay with the default `merge` unless replacing is the intent.
- `rr_netconf_mgmt` imports `libyang` and `ncclient`; on the local computer run it with the
  interpreter that `skills/netconf/scripts/setup.sh` creates (`<repo>/.venv/bin/python`).

### The same leaf does not look the same on both interfaces

Configuration values are *represented* differently by the two interfaces, so a value read over one
cannot be compared directly with a value written over the other:

| | RA2 API (`settings_get`) | NETCONF (`get_config`) |
|---|---|---|
| `Off`/`On` leaves | integer `0` / `1` | string `"Off"` / `"On"` |
| enumerations | integer, the API's own numbering | the YANG enum string |
| table (`RR_S_TABLE_*`) | list of row dicts under `config_data["main"]` | list under the same path, values as YANG strings |

The API's enum numbering is **not** the YANG enum order. On 2.3.10.0 `Routing_Mode` is `0` Static,
`1` WWAN (EXT), `2` WWAN (MAIN), while the YANG module lists WWAN (MAIN) before WWAN (EXT). Never
assume the index; the authoritative mapping travels with the reply in `config_meta`:

```python
result = device.settings_get().result
options = result.config_meta["main"]["children"]["RR_S_TABLE_RoutingRules"]["columns"]["Routing_Mode"]["options"]
label = next(o["label"] for o in options if o["value"] == row["Routing_Mode"])
```

Writing has the same trap: a save that sends `"On"` where the API uses `1` is rejected and the
leaf silently keeps its old value. Write back the shape you read.

## From a script running in the device

A script is started by the `sdk_launcher` daemon as a separate Linux process under a
dedicated user that matches the role of the user who created it. It is confined by AppArmor,
seccomp and `ulimit`, and it talks to the device's own management servers over `localhost`.
The launcher creates fresh credentials for every run and hands them to the script as one JSON
object on **standard input** (details in [writing-scripts.md](writing-scripts.md#the-stdin-payload)).
The script never sees a password and never stores anything.

```python
import json
import sys

payload = json.load(sys.stdin)        # read once, first thing, never print it
```

### RA2 API from inside the device

The launcher creates a RA2 API session token for the run and passes it as `ra2_api_token`. The
token is bound to the role of the instance, is valid only while the run lasts and is invalidated
when the run ends.

```python
from rr_ra2_mgmt.device import Device

with Device(host="localhost", token=payload["ra2_api_token"]) as device:
    info = device.device_info_get_private().result
    print(f"Running on {info.name}, firmware {info.firmware_version}", flush=True)
```

- With `token=` the library performs no login and no logout; `connect()` and `close()` are
  no-ops. The token's lifecycle belongs to the launcher.
- `Ra2SessionExpiredError` cannot be recovered from inside the script: there are no credentials
  to log in again. Log the error and exit with a non-zero code; the next run gets a new token.
- `host="localhost"` is the local device. To reach another device over the radio link put the
  remote address in `host=` and `intermediate="localhost"`; the token is checked on the local
  device, Remote Access must be enabled on the target.
- User name + password login also works from a script but is discouraged: the password would sit
  in the script source, which is stored in the device configuration, and it is not bound to the
  run. Use it only for connections to a foreign device that cannot be reached through
  `intermediate=`.
- Operations that need files from outside the device (firmware upload, configuration package
  restore) are of limited use inside a script.

### NETCONF from inside the device

The launcher provides everything needed for public-key SSH authentication to the local NETCONF
server: the port (`netconf_server_port`), the server's host key (`netconf_server_hostkey`), the
user to log in as (`user`) and a per-run private key (`private_key`).

```python
from rr_netconf_mgmt.device import NetconfDevice

with NetconfDevice(
    host="localhost",
    port=payload["netconf_server_port"],
    user=payload["user"],
    hostkey_b64=payload["netconf_server_hostkey"],
    key_base64=payload["private_key"].encode(),
) as device:
    config = device.get_config(xpath="/mbm-root:config_data/main/Main")
    print(config.get("/mbm-root:config_data/main/Main/RR_StationName"), flush=True)
```

- `hostkey_b64` turns host-key verification on with the key the launcher supplied; do not set
  `hostkey_verify=False` here.
- `key_base64` is forwarded to ncclient's libssh transport (`use_libssh=True`, the default);
  it expects `bytes`, hence `.encode()`.
- The NETCONF server closes idle sessions. A long-running script that polls occasionally should
  catch `NetconfMgmtSessionClosedError` and call `device.reconnect()`
  ([examples/netconf_poll_reconnect.py](examples/netconf_poll_reconnect.py)).
- What the script may read and write is governed by NACM according to the role of the instance;
  an `edit-config` outside the allowed subtree fails with `access-denied`.

### Connection parameters at a glance

| | RA2 API | NETCONF |
|---|---------|---------|
| local computer | `Device(HOST, USER, PASSWORD)` | `NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False)` |
| script in the device | `Device(host="localhost", token=payload["ra2_api_token"])` | `NetconfDevice(host="localhost", port=payload["netconf_server_port"], user=payload["user"], hostkey_b64=payload["netconf_server_hostkey"], key_base64=payload["private_key"].encode())` |
| credentials come from | `.env.ra2` / environment | stdin payload written by the launcher |
| session expiry | `reconnect()` (local) / exit non-zero (script) | `reconnect()` on `NetconfMgmtSessionClosedError` |
| another device over the radio | `intermediate=` | not available |
