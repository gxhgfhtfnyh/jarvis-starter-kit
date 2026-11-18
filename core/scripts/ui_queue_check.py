# -*- coding: utf-8 -*-
"""
Checks whether the jarvis-ui chat log holds UI-sent messages that the
terminal session hasn't processed yet. The reliable half of UI->terminal
delivery (2026-08-13): the UI writes every send to jarvis_ui_chat.jsonl
with source "ui" BEFORE attempting any input injection, so a session-side
Monitor arming `until python ui_queue_check.py; do sleep 2; done` wakes
Jarvis within seconds of a UI send -- no Windows input APIs involved.

Exit 0 (and print the pending messages) when there's something new;
exit 1 when there isn't. `--ack` advances the cursor after processing.
Cursor = byte offset into the chat log, stored in ui_queue_cursor.txt.
"""
import json
import sys
from pathlib import Path

CHAT_LOG = Path(r"J:\Jarvis2\logs\jarvis_ui_chat.jsonl")
CURSOR = Path(r"J:\Jarvis2\logs\ui_queue_cursor.txt")


def get_cursor():
    try:
        return int(CURSOR.read_text().strip())
    except (OSError, ValueError):
        return 0


def pending():
    if not CHAT_LOG.exists():
        return [], 0
    size = CHAT_LOG.stat().st_size
    cur = get_cursor()
    if cur > size:  # log truncated/cleared
        cur = 0
    msgs = []
    with open(CHAT_LOG, "r", encoding="utf-8") as f:
        f.seek(cur)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("role") == "user" and e.get("source") == "ui":
                msgs.append(e.get("text", ""))
        end = f.tell()
    return msgs, end


def main():
    if "--ack" in sys.argv:
        _, end = pending()
        CURSOR.write_text(str(end), encoding="utf-8")
        print(f"cursor -> {end}")
        return 0
    msgs, _ = pending()
    if msgs:
        for m in msgs:
            print(f"[UI] {m}")
        return 0
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
