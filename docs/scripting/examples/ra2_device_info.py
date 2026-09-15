"""RA2 API from inside the device: one call with the launcher's token, typed result, errors.

The launcher creates a session token for this run and passes it as `ra2_api_token`. With
`token=` the library performs no login or logout; the token dies when the run ends.
"""

import json
import sys

from rr_ra2_mgmt.device import Device
from rr_ra2_mgmt.exceptions import (
    Ra2CommunicationError,  # device unreachable (socket/TLS/timeout)
    Ra2MgmtError,  # common base class
    Ra2RpcError,  # request reached the device, it returned an error
    Ra2SessionExpiredError,  # token no longer valid (HTTP 401)
)

payload = json.load(sys.stdin)
sys.stdout.reconfigure(line_buffering=True)

try:
    with Device(host="localhost", token=payload["ra2_api_token"]) as device:
        envelope = device.device_info_get_private()
        info = envelope.result  # typed dataclass
        print(f"Station name:     {info.name}")
        print(f"Type:             {info.type}")
        print(f"Firmware version: {info.firmware_version}")
        print(f"Config version:   {info.config_version}")
        print(f"Device time:      {envelope.tstamp}")
        if envelope.warnings:
            print(f"Warnings:         {envelope.warnings}")
except Ra2SessionExpiredError:
    # The token belongs to the launcher; the script has no credentials to obtain a new one.
    print("RA2 API token expired, exiting", file=sys.stderr)
    sys.exit(2)
except Ra2RpcError as exc:
    print(f"device rejected the request: {exc.code} ({exc.message_id})", file=sys.stderr)
    sys.exit(1)
except Ra2CommunicationError as exc:
    print(f"cannot reach the local API: {exc}", file=sys.stderr)
    sys.exit(1)
except Ra2MgmtError as exc:
    print(f"ra2_mgmt error ({type(exc).__name__}): {exc}", file=sys.stderr)
    sys.exit(1)
