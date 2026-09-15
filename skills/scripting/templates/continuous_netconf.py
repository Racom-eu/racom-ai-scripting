"""__SCRIPT_NAME__ — continuous script with a long-lived NETCONF session.

Instance arguments (argparse):  --interval SECONDS   time between polls (default 600)

The NETCONF server closes idle sessions and a device restart closes them too; the next call
raises NetconfMgmtSessionClosedError and reconnect() re-establishes the session with the same
per-run key. RLIMIT_CPU and SIGTERM handling as for every continuous script.
"""

import argparse
import json
import resource
import signal
import sys
import time

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtError, NetconfMgmtSessionClosedError

LEAF_PATH = "/mbm-root:config_data/main/Main/RR_StationName"
stop_requested = False


def _on_sigterm(signum, frame):  # noqa: ARG001
    global stop_requested
    stop_requested = True


def main() -> int:
    payload = json.load(sys.stdin)
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
    resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))
    signal.signal(signal.SIGTERM, _on_sigterm)

    parser = argparse.ArgumentParser(description="__SCRIPT_NAME__")
    parser.add_argument("--interval", type=int, default=600)
    args = parser.parse_args()

    exit_code = 0
    try:
        with NetconfDevice(
            host="localhost",
            port=payload["netconf_server_port"],
            user=payload["user"],
            hostkey_b64=payload["netconf_server_hostkey"],
            key_base64=payload["private_key"].encode(),
        ) as device:
            while not stop_requested:
                try:
                    value = device.get_config(xpath=LEAF_PATH).get(LEAF_PATH)
                    print(f"{time.strftime('%H:%M:%S')} {LEAF_PATH} = {value!r}")
                except NetconfMgmtSessionClosedError as exc:
                    print(f"session closed by server ({exc}); reconnecting", file=sys.stderr)
                    device.reconnect()
                    continue
                for _ in range(args.interval):
                    if stop_requested:
                        break
                    time.sleep(1)
    except NetconfMgmtError as exc:
        print(f"netconf_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
        exit_code = 1
    print("stopped" if stop_requested else "finished")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
