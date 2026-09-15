# NETCONF on RipEx2 / RA2 — protocol notes

What goes over the wire and how the RACOM device models its data. Read this when `netconf rpc`
is needed, when an edit is rejected, or when a path does not resolve. Standards: RFC 6241
(NETCONF), RFC 6242 (over SSH), RFC 6020/7950 (YANG), RFC 6022 (NETCONF monitoring, `get-schema`).

## Transport and session

- SSH subsystem `netconf` on TCP **830**, password authentication with the device's web/API users.
  Devices have per-unit host keys; the CLI defaults to `hostkey_verify=False`
  (`RA2_NETCONF_HOSTKEY_VERIFY=1` to enforce known_hosts).
- Both sides exchange `<hello>` with capability URIs; `netconf ping` prints them. Expect at least
  `base:1.0`/`base:1.1`, `xpath`, `writable-running`, `ietf-netconf-monitoring`, and one
  `?module=<name>&revision=<date>` URI per YANG module.
- The server closes idle sessions. The library raises `NetconfMgmtSessionClosedError` on the next
  call; `device.reconnect()` re-establishes. `close_session` is best-effort with a 2 s timeout.
- Not thread-safe; one `NetconfDevice` per thread.

## Operations used

| RPC | library | CLI |
|-----|---------|-----|
| `<get-config source=running filter=xpath>` | `NetconfDevice.get_config(xpath, datastore)` → `NetconfConfig` | `netconf get`, `leaf` |
| `<edit-config target=running default-operation=merge|replace>` | `NetconfDevice.set_config(config, operation, datastore)` | `netconf set`, `edit` |
| `<get filter=subtree netconf-state/schemas>` | `NetconfDevice.get_module_list()` | `netconf modules` |
| `<get-schema identifier version format=yang>` | `NetconfDevice.get_schema(name, revision)` | `netconf schema` |
| `<get filter=xpath>` (operational data) | not wrapped — `manager.get(...)` | `netconf state` |
| any other RPC | not wrapped — `manager.dispatch(element)` | `netconf rpc` |

Datastore: `running` is the default and the one RipEx2 exposes for writes (writable-running).
Check `netconf ping` for `candidate`/`startup` before assuming they exist.

`default-operation`: **merge** (default) changes only the leaves present in the payload;
**replace** replaces the addressed subtree — everything you omit is reset to defaults. Never use
`replace` with a partial fragment unless that is the intent.

## Errors

A failed operation returns `<rpc-error>` with `error-type`, `error-tag`, `error-severity`,
`error-path`, `error-message`, `error-info`. ncclient raises `RPCError`; the library wraps it:

| library exception | typical cause |
|-------------------|---------------|
| `NetconfMgmtConnectionError` | SSH refused/timeout, wrong credentials, host key mismatch |
| `NetconfMgmtSessionClosedError` | idle timeout / device restart → `reconnect()` |
| `NetconfMgmtConfigError` | edit rejected (`invalid-value`, `unknown-element`, `missing-element`, `operation-failed`), unknown XPath prefix, XML does not match the YANG (missing schema), bad data path in `set()`/`get()` |
| `NetconfMgmtOperationError` | device does not answer NETCONF monitoring (`netconf-state/schemas` missing) |

Common `error-tag` meanings: `invalid-value` (type/range/enum/pattern violated),
`unknown-element` (typo in a node name or wrong namespace), `missing-element` (a mandatory leaf or
list key is absent), `data-missing`/`data-exists`, `access-denied`, `lock-denied`,
`operation-not-supported`. The `error-path` points at the offending node.

## Data model

Everything is YANG. Root module on RipEx2 today: **`mbm-root`** (namespace `eu:racom:mbm:root`),
importing `ietf-yang-types` and `ietf-inet-types`. Its top level:

```
module: mbm-root
  +--rw config_data            # the configuration — mirrors the web UI / HTTPS API config_data
  |  +--rw main
  |  |  +--rw Main             # RR_StationName, RR_StationDesc, RR_StationLocation, …
  |  |  +--rw EthPorts …       # one container per settings page
  |  +--rw auth, monitoring, device, region, com_N_prot, term_server_N_prot …
  +--ro Diagnostics            # operational data — read with <get> (netconf state)
```

Leaf names are the same `RR_*` identifiers the HTTPS API uses in `config_data` (see skills/api),
so knowledge transfers: `config_data.main.RR_StationName` there is
`/mbm-root:config_data/main/Main/RR_StationName` here (NETCONF adds the page container).
Enumerations are named typedefs (`lbox_off_on` → `Off`/`On`); `netconf find <name>` lists the
allowed enum values, type, default and description of a leaf.

### Path formats (three different ones — do not mix)

| where | format | example |
|-------|--------|---------|
| libyang **data path** (`NetconfConfig.get/set`, `netconf leaf/set/tree/find`) | prefix only at module boundary, list keys as `[key='v']` | `/mbm-root:config_data/main/Main/RR_StationName` |
| **XPath filter** (`get_config(xpath=…)`, `netconf get/state`) | same prefixes, `*` and predicates allowed; prefix must be a module the device advertises | `/mbm-root:config_data/main/Main`, `/mbm-root:*` |
| **as_dict() JSON** (`netconf get`, `update_from_dict`, `netconf edit file.json`) | module-prefixed top-level key only | `{"mbm-root:config_data": {"main": {"Main": {"RR_StationName": "x"}}}}` |

XML on the wire carries the namespace instead: `<config_data xmlns="eu:racom:mbm:root">…`.

### How the library reads a config

1. `get_module_list()` — names, revisions, namespaces of all YANG modules on the device.
2. `get-config` with the XPath filter (prefixes resolved to namespaces from step 1).
3. `get-schema` for every module whose namespace appears in the reply, plus imports on demand
   (libyang external module loader). Schemas are kept inside the `NetconfConfig`.
4. `as_dict()` / `get()` / `set()` / `update_from_dict()` parse the XML against those schemas in a
   fresh libyang context each time (typed values, validation of names).
5. `set_config()` wraps `config.xml` in `<nc:config>` and sends `edit-config`.

Consequences: a full `get_config()` without XPath downloads every schema (slow on radio links);
filter to the subtree you need. `update_from_dict` is **strict** — unknown nodes raise.

## Raw RPC cheat-sheet (`netconf rpc`)

```bash
netconf rpc '<get-schema xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"><identifier>mbm-root</identifier><format>yang</format></get-schema>'
netconf rpc '<get><filter type="subtree"><netconf-state xmlns="urn:ietf:params:xml:ns:yang:ietf-netconf-monitoring"><sessions/></netconf-state></filter></get>'
netconf rpc @edit.xml      # any RPC element from a file (edit-config, copy-config, lock, …)
```

The reply is printed as XML; `<ok/>` or `<data>…</data>` on success, `<rpc-error>` otherwise.

## Tools

- `yanglint` (built with libyang, `/usr/local/bin`) — validate/print YANG offline:
  `netconf schema mbm-root --out mbm-root.yang && yanglint -f tree mbm-root.yang` (needs the
  imported IETF modules in the same directory or `-p DIR`).
- Python `libyang` binding 4.0.0 needs the C library soversion ≥ 4.2.2 → `scripts/setup.sh` builds
  CESNET/libyang v4.2.2 from source because Ubuntu 24.04 ships 2.1.x.
