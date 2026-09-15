"""Store a local Python file as a script with one instance, over NETCONF (local computer).

Usage:
    deploy_script.py SCRIPT.py [--name NAME] [--instance INSTANCE]

Connection: RA2_HOST, RA2_USER, RA2_PASSWORD (and optionally RA2_NETCONF_PORT) from the
environment, e.g. `set -a; . ./.env.ra2; set +a`. Run with the repository's `.venv/bin/python`
(created by skills/netconf/scripts/setup.sh), which has rr_netconf_mgmt, ncclient and libyang.

The Scripting configuration is the `rr-sdk-launcher` YANG module. This tool sends an edit-config with
default-operation merge, so an existing script of the same name gets its source replaced and
other scripts and instances are untouched. Triggers and instance arguments are further nodes of
the same `instances` entry; look them up with `netconf tree /rr-sdk-launcher:sdk-launcher`
before adding them here.

This changes the device configuration. Scripting must be enabled (main / SDK / Sdk_Enable = On).
"""

import argparse
import os
import pathlib
import sys
import xml.etree.ElementTree as ET

from rr_netconf_mgmt.config import NetconfConfig
from rr_netconf_mgmt.device import NetconfDevice
from rr_netconf_mgmt.exceptions import NetconfMgmtError

LAUNCHER_NS = "eu:racom:common:rr-sdk-launcher"


def build_fragment(script_name: str, source: str, instance_name: str) -> str:
    """Return the <sdk-launcher> XML fragment; ElementTree escapes the source text."""
    ET.register_namespace("", LAUNCHER_NS)
    root = ET.Element(f"{{{LAUNCHER_NS}}}sdk-launcher")
    scripts = ET.SubElement(root, f"{{{LAUNCHER_NS}}}scripts")
    ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}name").text = script_name
    ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}source").text = source
    instances = ET.SubElement(scripts, f"{{{LAUNCHER_NS}}}instances")
    ET.SubElement(instances, f"{{{LAUNCHER_NS}}}name").text = instance_name
    return ET.tostring(root, encoding="unicode")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("script", type=pathlib.Path, help="local Python file to deploy")
    parser.add_argument("--name", help="script name in the device (default: file name)")
    parser.add_argument("--instance", default="default", help="instance name (default: default)")
    args = parser.parse_args()

    try:
        host = os.environ["RA2_HOST"]
        user = os.environ["RA2_USER"]
        password = os.environ["RA2_PASSWORD"]
    except KeyError as exc:
        print(f"missing environment variable {exc}; source .env.ra2 first", file=sys.stderr)
        return 1
    port = int(os.environ.get("RA2_NETCONF_PORT", "830"))

    script_name = args.name or args.script.name
    source = args.script.read_text(encoding="utf-8")
    fragment = build_fragment(script_name, source, args.instance)

    try:
        with NetconfDevice(host=host, port=port, user=user, password=password, hostkey_verify=False) as device:
            # NetconfConfig built from raw XML: set_config only needs the XML, no schemas.
            device.set_config(NetconfConfig(fragment))
    except NetconfMgmtError as exc:
        print(f"deploy failed ({type(exc).__name__}): {exc}", file=sys.stderr)
        return 1

    print(f"deployed {args.script} as script '{script_name}' with instance '{args.instance}' on {host}")
    print(f"start it with: ra2 call script_start script_name={script_name} instance_name={args.instance}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
