# -*- coding: utf-8 -*-
"""
Item 90 of the 2026-08-09 tools brainstorm: same one-call aggregation as
batch 7/8's security_digest.py/dev_digest.py, but for the Active Priorities
queue itself - open count, stale items, school-specific items, and
Reminders sync health, all in one pass.

Usage:
    python priorities_digest.py
"""
import sys

sys.path.insert(0, r"J:\Jarvis2\scripts")


def run_digest():
    lines = []

    from priority_age_tracker import check_ages, _open_item_keys
    open_items = _open_item_keys()
    stale = check_ages()
    lines.append(f"Open priority items: {len(open_items)} ({len(stale)} stale, 21+ days)")

    from school_digest import school_open_items
    school = school_open_items()
    lines.append(f"School-tagged open items: {len(school)}")

    from archive_completed_priorities import find_archivable
    archivable = find_archivable()
    lines.append(f"Completed items ready to archive: {len(archivable)}")

    try:
        from reminder_sync_health_check import check_sync
        sync = check_sync()
        if "error" in sync:
            lines.append(f"Reminders sync: {sync['error']}")
        else:
            lines.append(f"Reminders sync: {len(sync['missing_from_reminders'])} item(s) missing from Reminders")
    except Exception as e:
        lines.append(f"Reminders sync: check failed ({e})")

    return lines


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== Priorities Digest ===\n")
    for line in run_digest():
        print(line)
