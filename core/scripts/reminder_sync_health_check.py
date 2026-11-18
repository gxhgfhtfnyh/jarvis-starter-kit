# -*- coding: utf-8 -*-
"""
Item 86 of the 2026-08-09 tools brainstorm: sync_priorities_to_reminders.py
is push-only by design (reading iCloud Reminders back is a confirmed dead
end), which means it can silently drift if a reminder gets manually deleted
or completed on the Apple side - nothing currently notices that. Compares
Active Priorities.md's real open items against what the Reminders list
actually contains right now, reusing sync_priorities_to_reminders.py's own
real parse_open_tasks() and the same ICloudCalDAV interface it already uses.

Usage:
    from reminder_sync_health_check import check_sync
    python reminder_sync_health_check.py
"""
import sys

sys.path.insert(0, r"J:\Jarvis2\scripts")

from icloud_calendar import ICloudCalDAV
from sync_priorities_to_reminders import ACTIVE_PRIORITIES_PATH, parse_open_tasks


def check_sync():
    priorities = set(parse_open_tasks(ACTIVE_PRIORITIES_PATH))

    client = ICloudCalDAV()
    client.discover()
    reminders_list = client.find_calendar(name_contains="Reminders", component="VTODO")
    if not reminders_list:
        return {"error": "Could not find a Reminders list"}

    existing = client.list_todos(reminders_list["url"])
    reminder_titles = {t["summary"] for t in existing}

    return {
        "priorities_count": len(priorities),
        "reminders_count": len(reminder_titles),
        "missing_from_reminders": sorted(priorities - reminder_titles),
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    result = check_sync()
    if "error" in result:
        print(result["error"])
        sys.exit(1)
    print(f"Active Priorities open items: {result['priorities_count']}")
    print(f"Reminders list items: {result['reminders_count']}")
    if result["missing_from_reminders"]:
        print(f"\n{len(result['missing_from_reminders'])} item(s) in Active Priorities but missing from Reminders:")
        for t in result["missing_from_reminders"]:
            print(f"  {t}")
    else:
        print("\nIn sync.")
