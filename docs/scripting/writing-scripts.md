# Writing scripts

A script is a Python program whose source code is stored in the device configuration and
which the `sdk_launcher` daemon starts as a separate process when a trigger fires. This document
is the runtime contract a script has to follow. For how the script talks to the device see
[device-access.md](device-access.md); for ready-to-copy code see [examples/](examples/README.md).

## Vocabulary

| term | meaning |
|------|---------|
| **script** | one Python source file, stored as a UTF-8 string in the configuration list `scripts` of the `rr-sdk-launcher` YANG module; identified by a unique name such as `status_monitor.py` |
| **instance** | a use of a script with its own command-line arguments and start/stop triggers; list `scripts/instances`; identified by the pair (script name, instance name) |
| **trigger** | what starts or stops an instance: `scheduler` (crontab expression), `xpath` (a configuration or state leaf changed, or a YANG notification arrived), `manual` (an action invoked over the API), `system` (reboot, service stop, configuration removal) |
| **execution** | one run of an instance; recorded with start time, PID, trigger, state, exit code and the captured output |

Two shapes of scripts are typical:

- **continuous** scripts run until they are stopped: poll status, watch for a condition, log
  periodically, send notifications;
- **one-shot** scripts do a job and exit: adjust configuration when a trigger fires, maintenance,
  export data.

## Runtime environment

- **One file.** The whole script is a single source string; there is no package, no second
  module, no `pip`. Put shared code into the file itself.
- **Interpreter and modules.** The device firmware ships a reduced Python standard library plus
  a small set of third-party modules. Run [examples/list_modules.py](examples/list_modules.py) on
  the target firmware to see exactly what is importable before relying on a module. Firmware
  2.3.4.7 offered 125 stdlib modules (including `json`, `ssl`, `socket`, `sqlite3`, `subprocess`,
  `threading`, `asyncio`, `logging`, `xml`, `urllib`) and these third-party ones:
  `libyang`, `lxml`, `ncclient`, `ssh`, `sysrepo`, `peewee`, `cffi`, `api2yang`, `sdk_launcher`.
  Firmware 2.3.6.0 (Python 3.11.3) reports the same third-party set plus `pycparser`.
  The management libraries `rr_ra2_mgmt` and `rr_netconf_mgmt` used throughout these documents
  must appear in that listing on your firmware; if they do not, the firmware is too old.
  **Measured: 2.3.4.7 and 2.3.6.0 do not ship them; 2.3.10.0 does** — it reports 135 stdlib modules
  and adds `rr_ra2_mgmt`, `rr_netconf_mgmt`, `apscheduler` and `tzlocal` to the third-party set.
  So the examples below run as written on 2.3.10.0 and not on the two older versions. On a firmware
  without them, a script needing NETCONF can use `ncclient` directly with the payload's per-run key
  and parse replies with `lxml`.
- **The listing over-reports.** `list_modules.py` scans the directories on `sys.path`; it does not
  import anything. On 2.3.6.0 `sysrepo`, `libyang`, `api2yang` and `sdk_launcher` are listed but
  raise `PermissionError` inside a script, and `xml` is listed although `xml.etree` does not
  exist. `lxml`, `ncclient` and `ssh` do import. Confirm anything load-bearing with a throwaway
  script before building on it.
- **Closing a libssh (NETCONF) session defers its file-descriptor closes.** They happen when the
  session object is collected, by which time those numbers may belong to sockets opened since —
  measured on 2.3.6.0 resetting an unrelated established connection and invalidating a freshly
  created listening socket. A script that mixes `ncclient` with sockets of its own should call
  `gc.collect()` immediately after each session closes, while the descriptors it is about to
  close still belong to nobody.
- **User and file system.** The process runs as the Linux user of the role that created the
  script (for example `admin`), confined by an AppArmor profile whose default is "deny
  everything". Persistent files go to the working directory the launcher passes as
  `user_workdir` (under `/mnt/data/sdk/`, at most 10 MB per role). Assume nothing else is
  writable.
- **Network.** The device's own RA2 API and NETCONF server are reachable on `localhost`.
  Whether other hosts are reachable depends on the AppArmor profile and the device's routing.
- **Time.** The device clock and time zone are the device's; `Envelope.tstamp` on every RA2 API
  reply tells the device time.

## Process contract

### Command-line arguments

The `arguments` of an instance are passed as the process's command-line arguments and are
available in `sys.argv[1:]`. Use `argparse` so an instance can be parametrised without editing
the script. The launcher checks the argument syntax when the instance starts and refuses the
start if it is invalid.

### The stdin payload

Immediately after start, the launcher writes **one JSON object** to the script's standard input
and closes it. Read it first, before anything else touches `sys.stdin`:

```python
import json
import sys

payload = json.load(sys.stdin)
```

Keys used by the examples in this repository:

| key | meaning |
|-----|---------|
| `user` | user name the instance runs as; the NETCONF login name |
| `user_workdir` | absolute path of the persistent working directory of this role |
| `ra2_api_token` | RA2 API session token, valid for this run only, bound to the role of the instance. **Not sent by 2.3.6.0** (five keys only); **present on 2.3.10.0** (six keys) — read it with `payload.get()` so a script still runs where it is absent |
| `netconf_server_port` | TCP port of the local NETCONF server |
| `netconf_server_hostkey` | base64 public host key of the local NETCONF server, for `hostkey_b64=` |
| `private_key` | per-run private SSH key (PEM text) for NETCONF public-key authentication, for `key_base64=` |

The payload is the script's credential store. Never print it, never write it to the working
directory, never send it anywhere. Use `payload.get("key")` for anything optional so the script
keeps working when the launcher adds keys.

### Output

`stdout` and `stderr` are captured together, line by line, and stored with the execution record.
Read them with `script_execution_output_get` (RA2 API) or the `get-execution-logs` action.

- Standard output is a pipe, so Python buffers it. Print with `flush=True` or switch to line
  buffering once at the top of the script, otherwise output of a killed script is lost:

  ```python
  sys.stdout.reconfigure(line_buffering=True)
  sys.stderr.reconfigure(line_buffering=True)
  ```

- The launcher rate-limits output across all running scripts to 20 KiB/s or 1000 lines/s. The
  script that contributes most is blocked first; its dropped lines are replaced by one marker
  line `…[log section dropped due to system overload]…`. A traceback printed while the script is
  blocked is lost too, so log sparingly and prefer a summary line per iteration.
- The `logging` module works; direct it to `stdout` or `stderr` with a compact format.

### Exit code and state

Return `0` on success and a non-zero code on failure (`sys.exit(1)` or an uncaught exception). The
execution record shows `state` (`running`, `terminating`, `finished`, `killed`) and `exit_code`.
An execution ends as `killed` when the process was terminated by `SIGKILL`: after the stop
timeout, by the OOM killer, or by a power cycle.

### Signals and graceful stop

A stop (manual, trigger or system) delivers `SIGTERM`. If the process is still alive after a
timeout it receives `SIGKILL` and the execution is recorded as `killed`, possibly with output
missing. Continuous scripts should install a handler that ends the main loop and lets the
`with` blocks close their sessions:

```python
import signal

stop_requested = False

def _on_sigterm(signum, frame):
    global stop_requested
    stop_requested = True

signal.signal(signal.SIGTERM, _on_sigterm)

while not stop_requested:
    ...            # one iteration of work, then a short sleep
```

Keep `time.sleep()` intervals short (a few seconds) and loop, or the handler runs only when the
sleep ends and the stop timeout may expire first.

### Resource limits

Each execution runs under `ulimit` limits (soft/hard):

| resource | soft / hard | what it means for the script |
|----------|-------------|------------------------------|
| CPU time | 20 s / unlimited | a script that uses more than 20 s of CPU in total receives `SIGXCPU` and dies unless it raises the soft limit (see below) |
| address space | 512 MB / 1024 MB | `MemoryError` when exceeded |
| data segment | 64 MB / 96 MB | heap size |
| file size | 1 MB / 10 MB | `OSError` when a written file exceeds it |
| open files | 15 / 100 | each API session costs sockets; close what you open |
| processes | 20 / 20 | per Linux user, including subprocesses |
| stack | 2 MB / 4 MB | deep recursion fails |
| nice | 19 / 10 | scripts run at low priority |

Every **continuous** script should raise the CPU soft limit at start, otherwise it is killed after
about 20 s of accumulated CPU time regardless of wall time:

```python
import resource

resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
```

The working directory is limited to 10 MB per role; rotate or truncate what you write there.

### Concurrency

An instance runs at most once at a time; a start while it is `running` or `terminating` is
refused. The launcher also caps the number of instances running in parallel. `Device` and
`NetconfDevice` objects are not thread-safe; use one per thread if you use threads at all.

## Recommended script skeleton

```python
import argparse
import json
import resource
import signal
import sys
import time

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2MgmtError

INTERVAL_S = 60


def main() -> int:
    payload = json.load(sys.stdin)                      # 1. credentials, first
    sys.stdout.reconfigure(line_buffering=True)         # 2. output reaches the log immediately
    sys.stderr.reconfigure(line_buffering=True)
    resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

    parser = argparse.ArgumentParser()                  # 3. instance arguments
    parser.add_argument("--interval", type=int, default=INTERVAL_S)
    args = parser.parse_args()

    stop = {"requested": False}                         # 4. graceful stop
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__("requested", True))

    with Device(host="localhost", token=payload["ra2_api_token"]) as device:
        while not stop["requested"]:
            try:
                info = device.device_info_get_private().result
                print(f"{info.name}: ok")
            except Ra2MgmtError as exc:                 # 5. log, decide, continue or exit
                print(f"RA2 API error: {type(exc).__name__}: {exc}", file=sys.stderr)
                return 1
            for _ in range(args.interval):
                if stop["requested"]:
                    break
                time.sleep(1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## Deploying a script

The script source and its instances are configuration. There are two ways to write them:

1. **NETCONF `edit-config`** on the `rr-sdk-launcher` module. This is the method the
   [deploy example](examples/local/deploy_script.py) uses; the XML fragment looks like this
   (the `source` text must be XML-escaped):

   ```xml
   <sdk-launcher xmlns="eu:racom:common:rr-sdk-launcher">
     <scripts>
       <name>hello_world.py</name>
       <source>import time;print("Hello world!");time.sleep(20)</source>
       <instances>
         <name>ScriptA</name>
       </instances>
     </scripts>
   </sdk-launcher>
   ```

   Instance arguments and triggers are leaves and containers of the same `instances` entry.
   Look their exact names up in the model rather than guessing:
   `skills/netconf/scripts/netconf tree /rr-sdk-launcher:sdk-launcher` or
   `skills/netconf/scripts/netconf schema rr-sdk-launcher`.

2. **RA2 API** `settings_get` / `helper.settings_save`: the Scripting configuration is part of the
   device configuration document. Read the configuration, edit the Scripting section in place, write
   the whole `config_data` back. Use `skills/api/scripts/ra2 config keys` on the device to find
   the section.

Prerequisites on the device: `RR_Sdk_Enable` (RA2 API `config_data.main.RR_Sdk_Enable`, NETCONF
`/mbm-root:config_data/main/SDK/RR_Sdk_Enable`; `skills/scripting/scripts/scripting enable --on`) must be `On` (Admin role), and the user deploying the script needs a role at least as high as the
role the script should run with. Changing the NETCONF server configuration (on/off, port)
restarts the Scripting subsystem and with it all running instances.

A script has no explicit version; the launcher stores a hash of the source as `source-version`
and marks executions of an older source as `is_outdated`.

## Running and debugging

- **Start and stop** an instance over the RA2 API with `script_start(script_name, instance_name)`
  and `script_stop(...)`, or interactively with
  `skills/api/scripts/ra2 call script_start script_name=hello_world.py instance_name=ScriptA`.
  Both count as a manual trigger. The equivalent NETCONF actions `triggers/start/manual` and
  `triggers/stop/manual` exist in the YANG model.
- **List** scripts, instances and their last execution: `script_overview_get`.
- **Execution history**: `script_execution_get(pagination={"limit": 20}, filter={"instances": [{"script_name": "hello_world.py"}]})`.
- **Output** of one execution: `script_execution_output_get({"execution_id": ID}, {"limit": 1000})`,
  paged with `id_exclusive` (the id of the last line already read). The
  [run-and-fetch example](examples/local/run_and_fetch_output.py) chains these three calls.
- **Missing output** usually means buffering (add `flush=True` or line buffering) or the rate
  limiter; a state `killed` with exit code absent means `SIGKILL`, typically the stop timeout,
  `SIGXCPU` (CPU limit) or the OOM killer.
- **Start refused**: the instance is already running or terminating, the parallel-instance limit
  is reached, or the instance arguments do not parse.

## Checklist before deploying

- [ ] reads the stdin payload first and never prints or stores it
- [ ] enables line buffering or prints with `flush=True`
- [ ] handles `SIGTERM` if it runs longer than a few seconds
- [ ] raises `RLIMIT_CPU` if it is a continuous script
- [ ] connects to `localhost` with the token (RA2 API) or the per-run key (NETCONF); no passwords in the source
- [ ] catches `Ra2MgmtError` / `NetconfMgmtError`, exits non-zero on unrecoverable errors
- [ ] reconnects on `NetconfMgmtSessionClosedError` when it keeps a NETCONF session across long pauses
- [ ] writes files only under `user_workdir`, and not more than a few MB
- [ ] imports only modules present on the target firmware (verified with `list_modules.py`)
- [ ] logs a line per event or iteration, not per record
