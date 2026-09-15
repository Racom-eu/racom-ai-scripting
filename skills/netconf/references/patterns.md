# Working patterns and recipes (NETCONF)

CLI first, Python second. Python needs the interpreter from `scripts/setup.sh`
(`<repo>/.venv/bin/python`) — the library imports libyang and ncclient at module level.
Runnable examples: `rac_netconf_mgmt/examples/`.

## 0. Connection

Shared with the HTTPS-API skill: `.env.ra2` in the repo root (template `.env.ra2.example`) or
`RA2_*` env vars. NETCONF-specific: `RA2_NETCONF_PORT` (830), `RA2_NETCONF_HOSTKEY_VERIFY` (0);
`RA2_API_PORT` belongs to the HTTPS API only and is ignored here.
`netconf env` shows what is resolved; the password is masked.

The same device user/password works for HTTPS API and NETCONF. There are no public NETCONF
operations — every command logs in.

## 1. Is NETCONF up? What does the device model?

```bash
netconf ping                     # session id, base/xpath/writable-running capabilities, module count
netconf modules                  # every YANG module: name, revision, namespace
netconf modules racom            # filter by name/namespace regex
netconf tree --depth 2           # mbm-root top-level containers
netconf tree /mbm-root:config_data/main/Main        # one settings page with types
```

## 2. Find the path for a setting

```bash
netconf find StationName                       # → /mbm-root:config_data/main/Main/RR_StationName (string)
netconf find eth_autoneg --desc                # also search descriptions
netconf find RR_EthPort_1 --module mbm-root
netconf find Radio --offline                   # no device: bundled mbm-root example model
```

Output rows carry `path`, `kind`, `type`, `config`, `default`, `units`, `enum`, `description`.
Leaf names match the HTTPS API `config_data` keys (`RR_*`), so an HTTPS-side name usually finds
the NETCONF path directly.

## 3. Read configuration

```bash
netconf get /mbm-root:config_data/main/Main          # JSON, typed
netconf get /mbm-root:config_data/main/Main --xml    # wire XML
netconf leaf /mbm-root:config_data/main/Main/RR_StationName
netconf get                                          # whole running config (fetches all schemas: slow)
netconf get '/mbm-root:config_data/main/EthPorts' > ethports.json
```

Always filter with an XPath; the full config pulls every YANG module over the link.

## 4. Change configuration (destructive — confirm with the user first)

```bash
netconf set /mbm-root:config_data/main/Main/RR_StationName=Unit-7
netconf set /mbm-root:config_data/main/Main/RR_StationName=Unit-7 /mbm-root:config_data/main/Main/RR_StationDesc="Roof"
```

`set` = `get-config` of the parent container → `NetconfConfig.set()` (libyang validates the path
and type) → `edit-config merge` → re-read and report `old`/`new`/`device`. Invalid enum/range
values fail before anything is sent. Use the `enum` list from `netconf find` for enumerations.

From a file:

```bash
netconf edit change.xml                       # XML fragment, e.g. rac_netconf_mgmt/examples/mbm_station_name.xml
netconf edit change.json                      # {"mbm-root:config_data": {...}} as printed by `netconf get`
netconf edit page.json --operation replace    # replace the addressed subtree; omitted leaves reset!
```

Python (one session, dict round-trip):

```python
from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtConfigError

with NetconfDevice(host=HOST, user=USER, password=PASSWORD, hostkey_verify=False) as dev:
    cfg = dev.get_config(xpath="/mbm-root:config_data/main/Main")
    data = cfg.as_dict()
    data["mbm-root:config_data"]["main"]["Main"]["RR_StationName"] = "Unit-7"
    cfg.update_from_dict(data)               # strict: unknown keys raise NetconfMgmtConfigError
    dev.set_config(cfg)                      # merge into running
```

Or without the dict: `cfg.set("/mbm-root:config_data/main/Main/RR_StationName", "Unit-7")`.
`set()` takes strings; libyang converts to the leaf type.

## 5. Operational data

```bash
netconf state /mbm-root:Diagnostics                  # XML
netconf state /mbm-root:Diagnostics --json           # decoded via the device's schemas
netconf state                                        # everything <get> returns (large)
```

`<get>` is not wrapped by the library; the CLI calls ncclient directly and resolves XPath
prefixes through `get_module_list()`.

## 6. Schemas offline

```bash
netconf schema mbm-root --out mbm-root.yang
netconf schema ietf-yang-types --out ietf-yang-types.yang
yanglint -f tree -p . mbm-root.yang | less
```

`netconf tree`/`find` build the context from the device on every call (schemas are cached only
within one command). For repeated browsing, dump the modules once and use `yanglint`.

## 7. Anything else

```bash
netconf rpc '<lock><target><running/></target></lock>'
netconf rpc @my-rpc.xml
```

Reply XML is printed verbatim. Wrap sequences (lock → edit → unlock) in Python instead of
separate CLI calls — each CLI call is its own session.

## 8. Python session handling

```python
dev = NetconfDevice(host, user=u, password=p, hostkey_verify=False, timeout=30)
dev.connect()
try:
    ...
except NetconfMgmtSessionClosedError:
    dev.reconnect()        # idle timeout / device restart
finally:
    dev.close()
```

`with NetconfDevice(...) as dev:` does connect/close. Extra ncclient options go through
`**connect_kwargs` (e.g. `look_for_keys=False`, `allow_agent=False`, `device_params`).

## 9. From inside a script (code that runs in the device)

The launcher payload (stdin JSON) carries everything for **public-key** login to the local
NETCONF server. No password, host key verified against the launcher-supplied key:

```python
payload = json.load(sys.stdin)
with NetconfDevice(
    host="localhost",
    port=payload["netconf_server_port"],
    user=payload["user"],
    hostkey_b64=payload["netconf_server_hostkey"],          # turns verification ON with this key
    key_base64=payload["private_key"].encode(),             # bytes; forwarded to ncclient's libssh transport
) as dev:
    cfg = dev.get_config(xpath="/mbm-root:config_data/main/Main")
```

`hostkey_verify=False` is wrong here. NACM limits what the instance's role may read/write
(`access-denied`). Long-lived sessions: catch `NetconfMgmtSessionClosedError` → `reconnect()`.
Scripting's own configuration (scripts, instances, triggers) is the `rr-sdk-launcher` module:
`netconf tree /rr-sdk-launcher:sdk-launcher`; deploy with `/racom:scripting` (`scripting deploy`).

## 10. Gotchas

- Three path formats (data path / XPath / as_dict keys) — see protocol.md. A prefix on every
  segment (`/mbm-root:config_data/mbm-root:main`) is *wrong* for libyang data paths.
- The XPath prefix must equal the module **name** as advertised (`mbm-root`), not the YANG
  `prefix` statement (`rr`).
- `update_from_dict` is strict; `as_dict()` output edited in place is the safe input.
- `replace` with a partial fragment resets what you omit. Default to `merge`.
- Full `get_config()` without XPath is slow (all schemas). Filter.
- Session idle timeout closes the SSH channel; the next call raises `NetconfMgmtSessionClosedError`.
- Some config changes restart services or the radio; the NETCONF reply arrives before that
  happens. Re-read after a moment when verifying.
- HTTPS API and NETCONF edit the **same** configuration; a NETCONF change shows up in
  `ra2 config get` (skills/api) and vice versa.
- The libyang binding is pinned (4.0.0 ↔ C library ≥ 4.2.2). If `import libyang` fails after a
  container rebuild, rerun `scripts/setup.sh`.
