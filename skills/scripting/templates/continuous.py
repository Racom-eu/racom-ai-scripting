"""__SCRIPT_NAME__ — continuous script: poll over the RA2 API until stopped.

Instance arguments (argparse):
  --interval SECONDS   time between iterations (default 60)
  --remote IP          poll another device through this one (Remote Access must be enabled there)

Runtime contract (see skills/scripting/SKILL.md):
  * payload first, line buffering, argparse,
  * RLIMIT_CPU raised — otherwise SIGXCPU after ~20 s of accumulated CPU time,
  * SIGTERM handler — a stop (manual, trigger, reboot) must end the loop before the SIGKILL timeout,
  * sleep in 1 s steps so the handler is honoured quickly,
  * one summary line per iteration — output is rate-limited (20 KiB/s, 1000 lines/s) and stored.
"""

import argparse
import json
import resource
import signal
import sys
import time

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2CommunicationError, Ra2MgmtError, Ra2SessionExpiredError

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
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--remote", help="IP of a remote device reached through this one")
    args = parser.parse_args()

    if args.remote:
        device = Device(host=args.remote, token=payload["ra2_api_token"], intermediate="localhost", timeout=30)
    else:
        device = Device(host="localhost", token=payload["ra2_api_token"])

    exit_code = 0
    with device:
        print(f"started, interval {args.interval} s" + (f", target {args.remote}" if args.remote else ""))
        while not stop_requested:
            try:
                # --- one iteration of work -------------------------------------------------
                status = device.helper.status_info(["system_basic"])
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} system_basic: {len(status.get('system_basic', []))} record(s)")
                # -------------------------------------------------------------------------------
            except Ra2SessionExpiredError:
                print("RA2 API token expired; exiting so the next run gets a fresh one", file=sys.stderr)
                exit_code = 2
                break
            except Ra2CommunicationError as exc:
                print(f"communication error, retrying next round: {exc}", file=sys.stderr)
            except Ra2MgmtError as exc:
                print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
                exit_code = 1
                break
            for _ in range(args.interval):
                if stop_requested:
                    break
                time.sleep(1)
    print("stopped" if stop_requested else "finished")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
