"""Start a script instance, wait for it, print the execution record and its output (local computer).

Usage:
    run_and_fetch_output.py SCRIPT_NAME INSTANCE_NAME [--timeout SECONDS] [--no-start]

Connection: RA2_HOST, RA2_USER, RA2_PASSWORD (and optionally RA2_API_PORT, default 443) from the
environment (`set -a; . ./.env.ra2; set +a`).
Works with the system python3 (rr_ra2_mgmt is stdlib-only) when the library is importable, or with
the repository's `.venv/bin/python`.

Everything here is the RA2 API: script_start (a manual trigger), script_execution_get to find the
newest execution of the instance, script_execution_output_get to page through its lines.
With --no-start it only shows the last execution, which is handy for continuous scripts.
"""

import argparse
import os
import sys
import time

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import Ra2MgmtError, Ra2RpcError

FINAL_STATES = {"finished", "killed"}


def latest_execution(device: Device, script_name: str, instance_name: str):
    result = device.script_execution_get(
        pagination={"limit": 1},
        filter={"instances": [{"script_name": script_name, "instance_names": [instance_name]}], "include_outdated": True},
    ).result
    return result.executions[0] if result.executions else None


def print_output(device: Device, execution_id: int) -> None:
    last_id = None
    while True:
        pagination = {"limit": 1000}
        if last_id is not None:
            pagination["id_exclusive"] = last_id
        result = device.script_execution_output_get({"execution_id": execution_id}, pagination).result
        lines = result.lines or []
        for line in lines:
            print(f"  {line.id:6d} | {line.content}")
        if len(lines) < 1000:
            break
        last_id = lines[-1].id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("script_name")
    parser.add_argument("instance_name")
    parser.add_argument("--timeout", type=int, default=120, help="seconds to wait for the run to end")
    parser.add_argument("--no-start", action="store_true", help="do not start; show the last execution")
    args = parser.parse_args()

    try:
        host, user, password = os.environ["RA2_HOST"], os.environ["RA2_USER"], os.environ["RA2_PASSWORD"]
    except KeyError as exc:
        print(f"missing environment variable {exc}; source .env.ra2 first", file=sys.stderr)
        return 1
    port = os.environ.get("RA2_API_PORT", "443")
    if port != "443":
        host = f"{host}:{port}"  # rr_ra2_mgmt has no port argument; the port rides in the host string

    try:
        with Device(host, user, password, timeout=15, retries=1) as device:
            previous = latest_execution(device, args.script_name, args.instance_name)
            if not args.no_start:
                try:
                    device.script_start(args.script_name, args.instance_name)
                except Ra2RpcError as exc:
                    print(f"start refused: {exc.code} ({exc.message_id})", file=sys.stderr)
                    return 2
                print(f"started {args.script_name}/{args.instance_name}", flush=True)

            deadline = time.time() + args.timeout
            execution = None
            while time.time() < deadline:
                execution = latest_execution(device, args.script_name, args.instance_name)
                is_new = execution is not None and (previous is None or execution.id != previous.id)
                if args.no_start and execution is not None:
                    break
                if is_new and execution.state in FINAL_STATES:
                    break
                time.sleep(2)

            if execution is None:
                print("no execution record found", file=sys.stderr)
                return 3

            print(f"execution {execution.id}: state={execution.state} exit_code={execution.exit_code} "
                  f"duration_ms={execution.duration} start_trigger={execution.start_trigger.type} "
                  f"stop_trigger={execution.stop_trigger.type if execution.stop_trigger else None} "
                  f"arguments={execution.arguments!r} outdated={execution.is_outdated}")
            if execution.state not in FINAL_STATES:
                print("(still running; output so far)")
            print("output:")
            print_output(device, execution.id)
            return 0 if execution.exit_code in (0, None) else 1
    except Ra2MgmtError as exc:
        print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
