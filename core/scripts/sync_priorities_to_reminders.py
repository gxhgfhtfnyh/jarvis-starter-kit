# -*- coding: utf-8 -*-
"""
One-way push: Active Priorities.md's open top-level tasks -> iCloud Reminders
("Reminders (warning emoji)" list). Push-only, deliberately - reading existing
iCloud Reminders back via CalDAV is a confirmed dead end (see icloud_calendar.py /
Jarvis Capabilities), so this never tries to reconcile two-way, only ensure every
currently-open Active Priorities item has a matching reminder.

Idempotent: skips creating a reminder if one with the same title already exists in
the list (checked via list_todos() before adding), so re-running this doesn't create
duplicates.

Usage:
    python sync_priorities_to_reminders.py
"""
import re
import sys

sys.path.insert(0, r"J:\Jarvis2\scripts")
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from icloud_calendar import ICloudCalDAV

ACTIVE_PRIORITIES_PATH = r"J:\Jarvis-Brain\Active Priorities.md"


def parse_open_tasks(path):
    """Extracts top-level (not indented sub-bullet) open checkbox items under
    '### Open Tasks', stopping at the next '###' heading."""
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    tasks = []
    in_open_section = False
    for line in lines:
        if line.strip() == "### Open Tasks":
            in_open_section = True
            continue
        if in_open_section and line.startswith("###"):
            break
        if in_open_section:
            m = re.match(r"^- \[ \] (.+)$", line.rstrip("\n"))
            if m:
                # Strip markdown link syntax [[...]] and trailing colon-details for a clean title
                title = re.sub(r"\[\[([^\]]+)\]\]", r"\1", m.group(1))
                tasks.append(title.strip())
    return tasks


def main():
    tasks = parse_open_tasks(ACTIVE_PRIORITIES_PATH)
    print(f"Found {len(tasks)} open task(s) in Active Priorities.md")

    client = ICloudCalDAV()
    client.discover()
    reminders_list = client.find_calendar(name_contains="Reminders", component="VTODO")
    if not reminders_list:
        print("Could not find a Reminders list!")
        return 1

    existing = client.list_todos(reminders_list["url"])
    existing_titles = {t["summary"] for t in existing}

    added, skipped = 0, 0
    for task in tasks:
        if task in existing_titles:
            skipped += 1
            continue
        client.add_reminder(reminders_list["url"], task)
        added += 1
        print(f"  Added: {task[:80]}")

    print(f"\nDone: {added} added, {skipped} already existed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
