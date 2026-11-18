# -*- coding: utf-8 -*-
"""
Item 11 of the 2026-08-09 tools brainstorm: a merged inbox digest.
Genuinely only covers Gmail + Telegram programmatically - Outlook has no API
access (Graph blocked at the RP tenant level, confirmed earlier), only a live
browser read. This is honest about that rather than silently only doing part
of the job and calling it "unified."

Usage:
    python unified_inbox.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")

TELEGRAM_CHATS_SNAPSHOT = Path(r"J:\Jarvis2\logs\telegram_chats.json")


def gmail_digest(max_results=10):
    try:
        from gmail_api import list_unread
        return list_unread(max_results=max_results)
    except Exception as e:
        return [{"error": str(e)}]


def telegram_digest():
    """Reads the daemon's own chats snapshot rather than opening a connection -
    telegram_daemon.py holds the only Telethon session, a second connection
    throws 'database is locked' (see [[Jarvis Capabilities]]'s Telegram
    section). If the daemon isn't running, this snapshot just won't exist yet."""
    if not TELEGRAM_CHATS_SNAPSHOT.exists():
        return None
    try:
        chats = json.loads(TELEGRAM_CHATS_SNAPSHOT.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return [{"error": str(e)}]
    return [c for c in chats if c.get("unread_count", 0) > 0]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    gmail = gmail_digest()
    print(f"=== Gmail: {len(gmail)} unread ===")
    for m in gmail:
        if "error" in m:
            print(f"  (error: {m['error']})")
        else:
            print(f"  {m.get('from', '?')}: {m.get('subject', '(no subject)')}")

    telegram = telegram_digest()
    if telegram is None:
        print("\n=== Telegram: daemon not running, no snapshot yet ===")
    else:
        print(f"\n=== Telegram: {len(telegram)} chat(s) with unread messages ===")
        for c in telegram:
            if "error" in c:
                print(f"  (error: {c['error']})")
            else:
                print(f"  {c.get('name', '?')} ({c.get('unread_count', 0)} unread)")

    print("\n=== Outlook: needs a live browser check ===")
    print("  No Graph API access exists for the RP tenant - this half can't be pulled programmatically.")
