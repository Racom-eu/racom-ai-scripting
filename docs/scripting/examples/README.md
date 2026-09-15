# Examples

Scripts in this directory are **self-contained**: each one is a single file that can be stored as
a script as it is. Scripts under [local/](local/) run on your computer, not in the device.

## Scripts that run in the device

| file | shows |
|------|-------|
| [hello_world.py](hello_world.py) | the smallest possible script; prints and waits so the run can be observed |
| [list_modules.py](list_modules.py) | which Python modules the target firmware offers; run it before relying on an import |
| [script_context.py](script_context.py) | the process contract: stdin payload, arguments, working directory, limits, without leaking secrets; a template to start from |
| [ra2_device_info.py](ra2_device_info.py) | RA2 API from inside the device with the launcher's token; typed results and error handling |
| [ra2_station_name.py](ra2_station_name.py) | reading and changing configuration over the RA2 API (`settings_get` + `helper.settings_save`) |
| [ra2_status_monitor.py](ra2_status_monitor.py) | a continuous script: `SIGTERM` handling, CPU limit, periodic decoded status, remote device through `intermediate="localhost"` |
| [netconf_station_name.py](netconf_station_name.py) | reading and changing one configuration leaf over NETCONF with the per-run key |
| [netconf_poll_reconnect.py](netconf_poll_reconnect.py) | a long-lived NETCONF session that survives the server's idle timeout |

## Tools that run on a local computer

They need the repository's virtual environment (`bash skills/netconf/scripts/setup.sh` creates
`.venv` with both libraries) and the connection settings from `.env.ra2` or `RA2_*` variables
(`RA2_HOST`, `RA2_USER`, `RA2_PASSWORD`; optional `RA2_API_PORT` for the RA2 API and
`RA2_NETCONF_PORT` for NETCONF).

| file | shows |
|------|-------|
| [local/deploy_script.py](local/deploy_script.py) | NETCONF `edit-config`: store a local `.py` file as a script with one instance |
| [local/run_and_fetch_output.py](local/run_and_fetch_output.py) | RA2 API: start the instance, wait for it to finish, print the execution record and its output |

Typical loop while developing:

```bash
set -a; . ./.env.ra2; set +a
.venv/bin/python docs/scripting/examples/local/deploy_script.py docs/scripting/examples/ra2_device_info.py --instance test
.venv/bin/python docs/scripting/examples/local/run_and_fetch_output.py ra2_device_info.py test
```

Deploying a script and starting an instance change the device state; the device rejects both
without a sufficient role.
