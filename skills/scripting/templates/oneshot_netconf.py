"""__SCRIPT_NAME__ — one-shot script: read/change one configuration leaf over NETCONF.

Instance arguments (argparse):  --set VALUE   new value for LEAF_PATH (omit to only read)

The launcher supplies everything for public-key SSH login to the local NETCONF server:
port, the server's host key (verified), the user name and a private key valid for this run.
Edits touch one subtree: get-config with an XPath filter, set the leaf by its libyang data path
(validated against the YANG schema), edit-config with default-operation merge.
"""

import argparse
import json
import sys

from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtConfigError, NetconfMgmtError

LEAF_PATH = "/mbm-root:config_data/main/Main/RR_StationDesc"     # find paths with: netconf find <name>
SUBTREE = "/mbm-root:config_data/main/Main"


def main() -> int:
    payload = json.load(sys.stdin)
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="__SCRIPT_NAME__")
    parser.add_argument("--set", dest="new_value")
    args = parser.parse_args()

    try:
        with NetconfDevice(
            host="localhost",
            port=payload["netconf_server_port"],
            user=payload["user"],
            hostkey_b64=payload["netconf_server_hostkey"],
            key_base64=payload["private_key"].encode(),
        ) as device:
            config = device.get_config(xpath=SUBTREE)
            current = config.get(LEAF_PATH)
            print(f"{LEAF_PATH} = {current!r}")
            if args.new_value is not None and args.new_value != current:
                config.set(LEAF_PATH, args.new_value)
                try:
                    device.set_config(config)                  # edit-config merge
                except NetconfMgmtConfigError as exc:
                    print(f"edit rejected: {exc}", file=sys.stderr)
                    return 1
                print(f"{LEAF_PATH} = {device.get_config(xpath=SUBTREE).get(LEAF_PATH)!r} (after edit)")
    except NetconfMgmtError as exc:
        print(f"netconf_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
