# -*- coding: utf-8 -*-
"""Aircon wrapper (2026-10-30, the owner: "alwys toggle off and on"; the plain 'on' command reports success but the IR unit stays off because the hub
thinks it is already on). A tool instead of a memory note, so the right sequence happens without being told.
    python aircon.py on [--device "Son Air Con"] [--dry-run]     # off, wait 4 s, on
    python aircon.py off [--device "Son Air Con"] [--dry-run]    # plain off
Uses hag_control.ps1 (local hub, no browser). Prints what the hub replied; success from the hub is not proof the unit is running.
Pure core (unit tested): plan(action) -> list of ('send', state) / ('wait', seconds) steps."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
HAG = os.path.join(HERE, "hag_control.ps1")
DEFAULT_DEVICE = "Son Air Con"
WAIT_S = 4


def plan(action):
    """'on' -> off, wait, on (the IR blaster only fires when the hub state changes); 'off' -> off. Anything else -> []."""
    if action == "on":
        return [("send", "off"), ("wait", WAIT_S), ("send", "on")]
    if action == "off":
        return [("send", "off")]
    return []


def send(device, state):
    r = subprocess.run(["powershell", "-NoProfile", "-File", HAG, "-Device", device, "-State", state], capture_output=True, text=True, timeout=60)
    return (r.stdout or r.stderr or "").strip().splitlines()[-1:] or [""]


def main(argv):
    action = argv[0] if argv else ""
    steps = plan(action)
    if not steps:
        print("usage: aircon.py on|off [--device NAME] [--dry-run]")
        return 2
    device = argv[argv.index("--device") + 1] if "--device" in argv else DEFAULT_DEVICE
    dry = "--dry-run" in argv
    for kind, val in steps:
        if kind == "wait":
            print("wait %ss" % val)
            if not dry:
                time.sleep(val)
        else:
            print("%s -> %s" % (device, val), "" if dry else send(device, val)[0])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
