# -*- coding: utf-8 -*-
"""
Thin wrapper for a Monitor watcher, same role as ui_queue_check.py plays
for new UI messages -- prints a line only when security_digest.py's own
fingerprint+acknowledgment logic says something is genuinely NEW (not
just flagged at some point, and not something already reviewed), so a
Monitor loop tailing this can wake Jarvis the moment a real new finding
appears instead of the owner having to notice the HUD badge and say
something.

Usage (as a Monitor command):
    python security_alert_check.py
Prints nothing (exit 0) when nothing new; prints a one-line-per-source
summary when something genuinely new is flagged.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from security_digest import build_digest

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    digest, any_alert = build_digest()
    if any_alert:
        for line in digest.splitlines():
            if "(NEW/changed)" in line:
                print(line.strip())
