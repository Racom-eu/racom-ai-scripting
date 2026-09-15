# RipEx2 / RA2 Scripting

Documentation for writing Python scripts that run **inside** a RACOM RipEx2 / RA2 device under
the Scripting launcher, and for talking to the device from a **local computer** while developing,
deploying and debugging those scripts.

Every device exposes two management interfaces. Both are usable from a local computer and from a
script running in the device, but they serve different purposes:

| interface | transport | use it for |
|-----------|-----------|------------|
| **RA2 API** (Racom-RPC over HTTPS, `rr_ra2_mgmt` library, `ra2` CLI) | HTTPS, JSON-RPC, port 443 (`RA2_API_PORT`) | everything: device info, status, statistics, events, configuration read and write, script start/stop, execution logs, firmware, diagnostics |
| **NETCONF** (`rr_netconf_mgmt` library, `netconf` CLI) | SSH subsystem `netconf`, XML, YANG, port 830 (`RA2_NETCONF_PORT`) | configuration updates only: schema-validated `get-config` / `edit-config`, including Scripting's own `rr-sdk-launcher` configuration |

## Documents

| file | contents |
|------|----------|
| [device-access.md](device-access.md) | the two interfaces, how the connection differs between a local computer and a script in the device, when to use which |
| [writing-scripts.md](writing-scripts.md) | the runtime contract of a script: stdin payload, arguments, working directory, limits, signals, output, deployment and debugging |
| [examples/](examples/README.md) | self-contained scripts for the device and helper tools for the local computer |

## Quick start

1. Write a single-file Python script. Read the JSON payload from `stdin` first, then do the work.
   Start from [examples/script_context.py](examples/script_context.py).
2. Talk to the device from the script through `localhost`:
   - RA2 API: `Device(host="localhost", token=payload["ra2_api_token"])`
   - NETCONF: `NetconfDevice(host="localhost", port=payload["netconf_server_port"], user=payload["user"], hostkey_b64=payload["netconf_server_hostkey"], key_base64=payload["private_key"].encode())`
3. Deploy the script and an instance of it into the device configuration (NETCONF
   `edit-config` on `rr-sdk-launcher`, see [examples/local/deploy_script.py](examples/local/deploy_script.py)).
4. Start the instance and read its output over the RA2 API
   ([examples/local/run_and_fetch_output.py](examples/local/run_and_fetch_output.py)).

## Related material in this repository

- `skills/api/` — `ra2` CLI, `rr_ra2_mgmt` library (git submodule) and reference notes for the RA2 API.
- `skills/netconf/` — `netconf` CLI, `rr_netconf_mgmt` library (git submodule) and reference notes for NETCONF.
- Official RA2 API documentation: <https://ripex2-apidocs.racom.eu>.
- The formal specification of the Scripting configuration, state and actions is the YANG module
  `rr-sdk-launcher` (project `rac_yangsrc`). Dump it from a device with
  `skills/netconf/scripts/netconf schema rr-sdk-launcher`.
