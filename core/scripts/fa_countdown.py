# -*- coding: utf-8 -*-
"""
Days-remaining countdown for upcoming deadlines listed in Active Priorities.md
(the vault's system of record for open work). Parses lines shaped like
"Course Name: DD Mon YYYY, ..." under Open Tasks - matches the exact format
the FA schedule was written in on 2026-08-07.

Usage:
    python fa_countdown.py
    from fa_countdown import get_countdown
"""
import datetime as dt
import re
import sys
from pathlib import Path

PRIORITIES_PATH = Path(r"J:\Jarvis-Brain\Active Priorities.md")

# "  - A364 Analytical Chemistry: 17 Aug 2026, 1:00-2:30 PM, Venue W54A, Seat 15"
DATE_LINE = re.compile(
    r"^\s*-\s+(?P<label>[^:]+):\s*(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3})\s+(?P<year>\d{4})(?P<rest>.*)$"
)
MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1
)}


def get_countdown():
    text = PRIORITIES_PATH.read_text(encoding="utf-8")
    today = dt.date.today()
    items = []
    for line in text.splitlines():
        m = DATE_LINE.match(line)
        if not m:
            continue
        month = MONTHS.get(m.group("month"))
        if not month:
            continue
        try:
            deadline = dt.date(int(m.group("year")), month, int(m.group("day")))
        except ValueError:
            continue
        days_left = (deadline - today).days
        items.append({
            "label": m.group("label").strip(),
            "date": deadline.isoformat(),
            "days_left": days_left,
            "detail": m.group("rest").strip(" ,"),
        })
    items.sort(key=lambda x: x["days_left"])
    return items


def print_digest():
    items = get_countdown()
    if not items:
        print("No dated deadlines found in Active Priorities.md.")
        return
    print("=== Countdown ===")
    for it in items:
        if it["days_left"] < 0:
            when = f"{-it['days_left']}d ago"
        elif it["days_left"] == 0:
            when = "TODAY"
        else:
            when = f"in {it['days_left']}d"
        print(f"[{when:>9}] {it['label']} - {it['date']}" + (f" ({it['detail']})" if it["detail"] else ""))


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print_digest()
