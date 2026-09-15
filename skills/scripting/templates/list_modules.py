"""List the Python modules importable on this firmware.

Run this before relying on an import: the device ships a reduced standard library and only a
handful of third-party packages. Prints two groups, standard library and site-packages.
"""

import os
import sys


def scan_path(path: str) -> set[str]:
    modules: set[str] = set()
    try:
        entries = os.listdir(path)
    except OSError:
        return modules

    for entry in entries:
        if entry == "__pycache__":
            continue
        full = os.path.join(path, entry)
        if entry.endswith(".py") and entry != "__init__.py":
            modules.add(entry[:-3])
        elif entry.endswith(".pyc") and entry != "__init__.pyc":
            modules.add(entry[:-4])
        elif entry.endswith(".so"):
            modules.add(entry.split(".")[0])
        elif os.path.isdir(full):
            has_init = os.path.exists(os.path.join(full, "__init__.py")) or os.path.exists(
                os.path.join(full, "__init__.pyc")
            )
            if has_init:
                modules.add(entry)
    return modules


stdlib = set(sys.builtin_module_names)
third_party: set[str] = set()
for p in sys.path:
    if not p or not os.path.isdir(p):
        continue
    found = scan_path(p)
    if "site-packages" in p:
        third_party |= found
    else:
        stdlib |= found

print(f"Python {sys.version.split()[0]}")
print(f"Stdlib modules ({len(stdlib)}):")
print("  " + ", ".join(sorted(stdlib)))
print()
print(f"3rd party modules ({len(third_party)}):")
print("  " + ", ".join(sorted(third_party)))
