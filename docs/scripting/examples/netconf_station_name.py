"""Read and change one configuration leaf over NETCONF from inside the device.

Instance arguments:  --set NAME   change RR_StationName to NAME (omit to only read)

The launcher supplies everything for public-key SSH authentication to the local NETCONF
server: port, the server's host key, the login name and a private key valid for this run.
NETCONF edits one subtree at a time: fetch the `Main` container with an XPath filter, set the
leaf by its data path (libyang validates path and type), send edit-config with merge.
"""

import argparse
import json
import sys

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtConfigError, NetconfMgmtError

NAME_PATH = "/mbm-root:config_data/main/Main/RR_StationName"

payload = json.load(sys.stdin)
sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser()
parser.add_argument("--set", dest="new_name", help="new station name")
args = parser.parse_args()

try:
    with NetconfDevice(
        host="localhost",
        port=payload["netconf_server_port"],
        user=payload["user"],
        hostkey_b64=payload["netconf_server_hostkey"],  # verifies the server against the launcher-supplied key
        key_base64=payload["private_key"].encode(),  # per-run private key, libssh transport
    ) as device:
        config = device.get_config(xpath="/mbm-root:config_data/main/Main")
        current = config.get(NAME_PATH)
        print(f"current station name: {current}")

        if args.new_name and args.new_name != current:
            config.set(NAME_PATH, args.new_name)
            try:
                device.set_config(config)  # edit-config, default-operation merge
            except NetconfMgmtConfigError as exc:
                print(f"edit rejected: {exc}", file=sys.stderr)
                sys.exit(1)
            check = device.get_config(xpath="/mbm-root:config_data/main/Main")
            print(f"new station name:     {check.get(NAME_PATH)}")
except NetconfMgmtError as exc:
    print(f"netconf_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
    sys.exit(1)
