"""Read and change configuration over the RA2 API from inside the device.

Instance arguments:  --set NAME   change RR_StationName to NAME (omit to only read)

Configuration over the RA2 API is one document: read `config_data`, change fields in place,
write the whole document back with helper.settings_save (asynchronous init + reconnect on the
device side, the helper waits for the result). A validation error means nothing was saved and
the individual violations are in `suberrors`.
"""

import argparse
import json
import sys

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2MgmtError, Ra2RpcError

payload = json.load(sys.stdin)
sys.stdout.reconfigure(line_buffering=True)

parser = argparse.ArgumentParser()
parser.add_argument("--set", dest="new_name", help="new station name")
args = parser.parse_args()

try:
    with Device(host="localhost", token=payload["ra2_api_token"]) as device:
        config = device.settings_get().result.config_data
        current = config["main"]["RR_StationName"]
        print(f"current station name: {current}")

        if args.new_name and args.new_name != current:
            config["main"]["RR_StationName"] = args.new_name
            try:
                saved = device.helper.settings_save(config).result
            except Ra2RpcError as exc:
                print(f"rejected: {exc.code} ({exc.message_id})", file=sys.stderr)
                for sub in exc.suberrors or []:
                    print(f"  - {sub.message_id} at {sub.seids}: {sub.message_params}", file=sys.stderr)
                sys.exit(1)
            print(f"new station name:     {saved.config_data['main']['RR_StationName']}")
except Ra2MgmtError as exc:
    print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
    sys.exit(1)
