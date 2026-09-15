"""Template: everything the launcher hands to a script, printed without leaking secrets.

Instance arguments:  --greeting TEXT   (optional, demonstrates argparse)

Shows the order of operations every script should follow:
  1. read the JSON payload from stdin (credentials for this run),
  2. make stdout/stderr line-buffered so output reaches the execution log immediately,
  3. parse the instance arguments,
  4. do the work inside the working directory.
"""

import argparse
import json
import os
import resource
import sys

# 1. The launcher writes one JSON object to stdin and closes it. Read it first.
payload = json.load(sys.stdin)

# 2. stdout is a pipe: without this, prints are block-buffered and lost if the process is killed.
sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# 3. Instance "arguments" arrive as command-line arguments.
parser = argparse.ArgumentParser(description="script context demo")
parser.add_argument("--greeting", default="Hello")
args = parser.parse_args()

# 4. Report what we got. Secrets are reported by presence and length only.
SECRET_KEYS = {"ra2_api_token", "private_key", "netconf_server_hostkey"}
print(f"{args.greeting} from a script (pid {os.getpid()})")
print("payload keys:")
for key in sorted(payload):
    value = payload[key]
    if key in SECRET_KEYS:
        print(f"  {key}: <{len(str(value))} chars, not shown>")
    else:
        print(f"  {key}: {value}")

workdir = payload.get("user_workdir")
if workdir:
    marker = os.path.join(workdir, "script_context.last_run")
    with open(marker, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()}\n")
    print(f"wrote {marker}")

soft, hard = resource.getrlimit(resource.RLIMIT_CPU)
print(f"CPU limit soft/hard: {soft}/{hard} s (raise the soft limit in long-running scripts)")
soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
print(f"open files soft/hard: {soft}/{hard}")
