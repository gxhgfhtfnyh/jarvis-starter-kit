# -*- coding: utf-8 -*-
"""Sets the Loom/Jarvis Discord bot's custom status via the daemon's own
outbox (presence is gateway-only, no REST endpoint exists for it, so this
can't be a direct API call like the bio/avatar setters -- discord_daemon.py's
_handle_outbox picks up {"status": <text>} and calls change_presence() on its
already-open connection). Standing permission from the owner, 2026-08-16:
"feel free to set your own statuses."

Usage:
    python discord_set_status.py "<status text>"
    python discord_set_status.py ""   (clears the custom status)
"""
import json
import sys
import time
from pathlib import Path

OUTBOX_DIR = Path(r"J:\Jarvis2\logs\discord_outbox")

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] in ("-h", "--help"):       # a help flag must never become the public status (2026-10-29)
        print(__doc__)
        sys.exit(0)
    status = sys.argv[1]
    OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{int(time.time() * 1000)}_status.json"
    tmp = OUTBOX_DIR / (fname + ".tmp")
    final = OUTBOX_DIR / fname
    tmp.write_text(json.dumps({"status": status}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(final)
    print(f"QUEUED status: {status!r}")
