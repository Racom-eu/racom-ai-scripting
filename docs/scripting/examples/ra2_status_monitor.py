"""Continuous script: periodically log decoded status over the RA2 API until stopped.

Instance arguments:
  --interval SECONDS   time between polls (default 60)
  --types T [T ...]    status_info types to decode (default system_basic)
  --remote IP          poll another device over the radio link through this one
                       (Remote Access must be enabled on the target)

Demonstrates what every long-running script needs:
  * RLIMIT_CPU raised, otherwise the process gets SIGXCPU after ~20 s of CPU time,
  * a SIGTERM handler so a stop (manual trigger, reboot, config removal) ends the loop cleanly,
  * a sleep split into 1 s steps so the handler is honoured quickly,
  * one summary line per iteration to stay far below the output rate limit.
"""

import argparse
import json
import resource
import signal
import sys
import time

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2CommunicationError, Ra2MgmtError, Ra2SessionExpiredError

payload = json.load(sys.stdin)
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)
resource.setrlimit(resource.RLIMIT_CPU, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

parser = argparse.ArgumentParser()
parser.add_argument("--interval", type=int, default=60)
parser.add_argument("--types", nargs="+", default=["system_basic"])
parser.add_argument("--remote", help="IP of a remote device reached through this one")
args = parser.parse_args()

stop_requested = False


def _on_sigterm(signum, frame):  # noqa: ARG001
    global stop_requested
    stop_requested = True


signal.signal(signal.SIGTERM, _on_sigterm)

if args.remote:
    # Requests go to the local device (intermediate) which forwards them to the target.
    device = Device(host=args.remote, token=payload["ra2_api_token"], intermediate="localhost", timeout=30)
else:
    device = Device(host="localhost", token=payload["ra2_api_token"])

exit_code = 0
with device:
    print(f"monitoring {', '.join(args.types)} every {args.interval} s" + (f" on {args.remote}" if args.remote else ""))
    while not stop_requested:
        try:
            status = device.helper.status_info(args.types)  # dict: type -> list of decoded records
            summary = "; ".join(f"{name}: {len(records)} record(s)" for name, records in status.items())
            print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {summary}")
            for name, records in status.items():
                if records:
                    print(f"  {name}[0] = {records[0]}")
        except Ra2SessionExpiredError:
            # No credentials to log in again; let the next run get a fresh token.
            print("RA2 API token expired, exiting", file=sys.stderr)
            exit_code = 2
            break
        except Ra2CommunicationError as exc:
            # Transient (target unreachable, service restarting): log and retry next round.
            print(f"communication error, will retry: {exc}", file=sys.stderr)
        except Ra2MgmtError as exc:
            print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
            exit_code = 1
            break

        for _ in range(args.interval):
            if stop_requested:
                break
            time.sleep(1)

print("stopped" if stop_requested else "finished")
sys.exit(exit_code)
