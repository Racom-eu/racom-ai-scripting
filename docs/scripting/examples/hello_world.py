"""Smallest script: print one line and stay alive long enough to be observed.

While it sleeps, the instance shows state "running" in script_overview_get and can be
stopped with script_stop (a manual trigger), which delivers SIGTERM.
"""

import time

print("Hello world!", flush=True)
time.sleep(20)
