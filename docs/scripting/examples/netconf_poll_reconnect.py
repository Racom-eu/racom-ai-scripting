"""Long-lived NETCONF session that survives the server's idle timeout.

Instance arguments:  --interval SECONDS   time between polls (default 600)

The NETCONF server closes sessions that stay idle, and a device restart closes them too. The
next call then raises NetconfMgmtSessionClosedError; reconnect() re-establishes the session
with the same per-run key and the loop continues.
"""

import argparse
import json
import resource
import signal
import sys
import time

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtError, NetconfMgmtSessionClosedError

NAME_PATH = "/mbm-root:config_data/main/Main/RR_StationName"

payload = json.load(sys.stdin)
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

parser = argparse.ArgumentParser()
parser.add_argument("--interval", type=int, default=600)
args = parser.parse_args()

stop_requested = False


def _on_sigterm(signum, frame):  # noqa: ARG001
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGTERM, _on_sigterm)

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
                config = device.get_config(xpath=NAME_PATH)
                print(f"{time.strftime('%H:%M:%S')} station name: {config.get(NAME_PATH)}")
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
sys.exit(exit_code)
