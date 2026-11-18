# -*- coding: utf-8 -*-
"""Last-ran check for every Jarvis scheduled task (2026-10-29, round-1 item 6).
    python jobs_lastrun.py       # one line per task; exit 1 if any failed its last run (result not 0 / 267009 running / 267011 never run)
Facts only, read-only."""
import csv
import io
import subprocess
import sys

OK_CODES = {"0", "267009", "267011"}
# This task calls self_test.py, which calls this script, to check "did every Jarvis task run OK last time" --
# including JarvisSelfTest's own prior run. A bad run anywhere makes this task itself exit 1, which schtasks then
# remembers as JarvisSelfTest's own "Last Result" -- so the NEXT run sees that stale FAIL, fails again purely from
# checking its own history, and the loop never self-heals even after the real cause is long fixed (found 2026-11-05,
# a genuinely different task's bad run from 07:50 that morning left JarvisSelfTest permanently red). Excluded by name
# from the health count for this structural reason, not because its own runs don't matter.
SELF_EXCLUDE = {r"\JarvisSelfTest"}
# Known failures (2026-10-29), reported separately so only NEW failures trip the self-test:
#  - Highest-privilege tasks cannot see the J: vault drive from an elevated token (exit 2); editing them needs admin.
#  - At-logon tasks run before the vault is unlocked and mounted.
KNOWN = {r"\Jarvis Threat Response": "elevated task cannot see J: (replaced by JarvisThreatResponseLimited; old one can only be removed with admin)",
         r"\Jarvis Defender Exclusion Check": "elevated task cannot see J: (replaced by JarvisDefenderExclusionLimited)",
         r"\Jarvis Hotkey Listener": "at-logon, vault not mounted yet", r"\Jarvis Clipboard Scrubber": "at-logon, vault not mounted yet",
         r"\Jarvis Daemons At Logon": "at-logon, vault not mounted yet",
         r"\JarvisLibreHardwareMonitor": "stale at-logon launch code 267014; verified 2026-11-14 the LibreHardwareMonitor.exe process is running",
         r"\JarvisGpsPresenceWebhook": "stale at-logon launch code; verified 2026-11-14 gps_presence_webhook.py process is running",
         r"\Jarvis Room Security Check": "away mode only: no Tapo camera credential (keyring service tapo_camera_study) on this PC; needs him to supply it"}  # 0 success, 267009 currently running, 267011 not yet run


def main():
    r = subprocess.run(["schtasks", "/query", "/v", "/fo", "csv"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    rows = list(csv.DictReader(io.StringIO(r.stdout)))
    seen, bad = set(), 0
    for row in rows:
        name = (row.get("TaskName") or "").strip()
        if "jarvis" not in name.lower() or name in seen:
            continue
        seen.add(name)
        code = (row.get("Last Result") or "").strip()
        ok = code in OK_CODES
        known = (not ok) and name in KNOWN
        self_excluded = name in SELF_EXCLUDE
        bad += 0 if (ok or known or self_excluded) else 1
        label = "OK  " if ok else ("SELF" if self_excluded else ("KNOWN" if known else "FAIL"))
        print(label, name, "| last run", row.get("Last Run Time"), "| result", code, "| next", row.get("Next Run Time"))
    print(f"{len(seen) - bad}/{len(seen)} Jarvis tasks healthy ({sum(1 for n in seen if n in KNOWN)} known issues listed above)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
