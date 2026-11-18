# -*- coding: utf-8 -*-
"""Turn the PC's motherboard RGB (Gigabyte B850M AORUS, ITE IT5711 chip) on/off by presence.
Built 2026-11-05: OpenRGB can't drive this chip (confirmed open upstream gap), so this goes
through the vendor tool (GIGABYTE Control Center) instead, via the already-working elevated
runner (J:\\Jarvis2\\scripts\\elevated_runner.ps1 + the JarvisElevated scheduled task) -- GCC.exe
needs admin to talk to the controller, and once it's running elevated my own session's synthetic
clicks can't reach its window anyway (Windows UIPI blocks lower-integrity input into a
higher-integrity window), so this edits the saved lighting profile directly and relaunches GCC
to apply it, rather than driving the GUI.

off(): zeroes brightness in the live profile (backed up once, first call) and relaunches GCC.
on(): restores the original backed-up profile and relaunches GCC.

Usage: python rgb_control.py on|off
"""
import json
import pathlib
import subprocess
import sys
import time

LOGS = pathlib.Path(r"J:\Jarvis2\logs")
REQ_FILE = pathlib.Path(r"C:\Users\kumar\JarvisSync\Jarvis2\logs\elevated_request.json")
RESULT_FILE = pathlib.Path(r"C:\Users\kumar\JarvisSync\Jarvis2\logs\elevated_result.txt")
BACKUP = LOGS / "Profile-0.xml.bak-20261105"
LIVE_PROFILE = pathlib.Path(r"C:\Program Files\GIGABYTE\Control Center\RGBMotherboard\Profile-0.xml")


def _dispatch(req_type, wait_s=8):
    REQ_FILE.write_text(json.dumps({"type": req_type}), encoding="utf-8")
    subprocess.run(["schtasks", "/run", "/tn", "JarvisElevated"], capture_output=True, timeout=30)
    time.sleep(wait_s)
    try:
        return RESULT_FILE.read_text(encoding="utf-8")
    except OSError:
        return "(no result file)"


def off():
    if not BACKUP.exists():
        # first-ever off: snapshot the current (lights-on) profile so on() has something to restore
        try:
            BACKUP.write_text(LIVE_PROFILE.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError as e:
            print("WARNING: could not back up live profile before turning off (%r) - on() will have nothing to restore" % e)
    print(_dispatch("stop-gcc"))
    print(_dispatch("set-gcc-brightness-zero"))
    print(_dispatch("launch-gcc"))


def on():
    if not BACKUP.exists():
        print("no backed-up profile to restore - never turned off yet via this script")
        return
    print(_dispatch("stop-gcc"))
    print(_dispatch("restore-gcc-profile"))
    print(_dispatch("launch-gcc"))


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in ("on", "off"):
        sys.exit("Usage: python rgb_control.py on|off")
    (on if sys.argv[1] == "on" else off)()
