# -*- coding: utf-8 -*-
"""Same role as discord_queue_check.py, for discord_daemon_newpc.py's own
incoming log (the second Jarvis bot, 2026-08-25) -- lets combined_watcher.py
wake a session the moment a real message lands in the shared channel(s),
same debounce/cursor mechanics, just pointed at the new bot's own files so
nothing collides with the original daemon's queue.

Exit 0 (and print the pending messages) when there's something new; exit 1
when there isn't. `--ack` advances the cursor after processing.
"""
import json
import sys
import time
from pathlib import Path

INCOMING_LOG = Path(r"J:\Jarvis2\logs\discord_newpc_incoming.jsonl")
CURSOR = Path(r"J:\Jarvis2\logs\discord_newpc_queue_cursor.txt")
SHOWN = Path(r"J:\Jarvis2\logs\discord_newpc_queue_shown.txt")
DEBOUNCE_SECONDS = 2.0
DEBOUNCE_POLL_SECONDS = 0.4
MAX_WAIT_SECONDS = 6.0


def get_cursor():
    try:
        return int(CURSOR.read_text().strip())
    except (OSError, ValueError):
        return 0


def pending():
    if not INCOMING_LOG.exists():
        return [], 0
    size = INCOMING_LOG.stat().st_size
    cur = get_cursor()
    if cur > size:
        cur = 0
    msgs = []
    with open(INCOMING_LOG, "r", encoding="utf-8") as f:
        f.seek(cur)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = e.get("text", "")
            prefix = f"(id {e.get('id')}"
            if e.get("reply_to"):
                prefix += f", replying to {e['reply_to']}"
            prefix += f", from {e.get('author_name', 'unknown')}"
            if e.get("author_is_bot"):
                prefix += " [sibling bot]"
            if e.get("mentions_me"):
                prefix += " [MENTIONS YOU]"
            prefix += ") "
            msgs.append(prefix + text)
        end = f.tell()
    return msgs, end


def _wait_for_quiet():
    started = time.time()
    last_size = INCOMING_LOG.stat().st_size if INCOMING_LOG.exists() else 0
    last_change = time.time()
    while time.time() - last_change < DEBOUNCE_SECONDS:
        if time.time() - started >= MAX_WAIT_SECONDS:
            break
        time.sleep(DEBOUNCE_POLL_SECONDS)
        size = INCOMING_LOG.stat().st_size if INCOMING_LOG.exists() else last_size
        if size != last_size:
            last_size = size
            last_change = time.time()


def main():
    if "--ack" in sys.argv:
        if not SHOWN.exists():
            return 0
        end = SHOWN.read_text().strip()
        CURSOR.write_text(end, encoding="utf-8")
        SHOWN.unlink(missing_ok=True)
        print(f"cursor -> {end}")
        return 0
    msgs, _ = pending()
    if not msgs:
        return 1
    _wait_for_quiet()
    msgs, end = pending()
    SHOWN.write_text(str(end), encoding="utf-8")
    for m in msgs:
        print(f"[DISCORD-NEWPC] {m}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
