"""__SCRIPT_NAME__ — one-shot script: do one job over the RA2 API and exit.

Instance arguments (argparse):  --example VALUE

Runtime contract (see skills/scripting/SKILL.md):
  1. read the launcher payload from stdin first (credentials for this run; never print it),
  2. line-buffer stdout/stderr so output reaches the execution log even if the run is killed,
  3. parse instance arguments,
  4. talk to the local device through localhost with the per-run token,
  5. exit 0 on success, non-zero on failure.
"""

import argparse
import json
import sys

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2MgmtError, Ra2RpcError, Ra2SessionExpiredError


def main() -> int:
    payload = json.load(sys.stdin)                       # 1
    sys.stdout.reconfigure(line_buffering=True)          # 2
    sys.stderr.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="__SCRIPT_NAME__")   # 3
    parser.add_argument("--example", default="value")
    args = parser.parse_args()

    try:
        with Device(host="localhost", token=payload["ra2_api_token"]) as device:   # 4
            info = device.device_info_get_private().result
            print(f"running on {info.name} (fw {info.firmware_version}), example={args.example}")
            # --- your work here -------------------------------------------------------------
            # cfg = device.settings_get().result.config_data        # read config
            # cfg["main"]["RR_StationDesc"] = "..."                  # edit in place
            # device.helper.settings_save(cfg)                       # write back (validated)
            # status = device.helper.status_info(["system_basic"])   # decoded status
            # ---------------------------------------------------------------------------------
    except Ra2SessionExpiredError:
        print("RA2 API token expired; the launcher issues a new one on the next run", file=sys.stderr)
        return 2
    except Ra2RpcError as exc:
        print(f"device rejected the request: {exc.code} ({exc.message_id})", file=sys.stderr)
        for sub in exc.suberrors or []:
            print(f"  - {sub.message_id} at {sub.seids}: {sub.message_params}", file=sys.stderr)
        return 1
    except Ra2MgmtError as exc:
        print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1
    return 0                                             # 5


if __name__ == "__main__":
    sys.exit(main())
