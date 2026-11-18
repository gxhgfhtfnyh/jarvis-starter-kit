# -*- coding: utf-8 -*-
"""
Item 1 of the 2026-08-09 tools brainstorm: a single "what's actually coming
up" view merging Active Priorities.md with real iCloud Calendar events
(via the existing icloud_calendar.py CalDAV client).

Deliberately does NOT include Outlook or POLITEmall deadlines - neither has
real programmatic access (Outlook's Graph API is blocked at the RP tenant
level; POLITEmall only reads live inside an interactive Playwright session,
confirmed by reading politemall_poll.py's own docstring). Those two need a
manual live-session refresh; this tool is honest about that gap rather than
silently omitting them without saying so.

Usage:
    python unified_deadlines.py [--days N]
"""
import datetime as dt
import re
import sys
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")

PRIORITIES_PATH = Path(r"J:\Jarvis-Brain\Active Priorities.md")
DATE_LINE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _priorities_deadlines(days_ahead):
    today = dt.date.today()
    cutoff = today + dt.timedelta(days=days_ahead)
    results = []
    try:
        text = PRIORITIES_PATH.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return results
    for line in text.splitlines():
        if "- [x]" in line:
            continue  # already done
        m = DATE_LINE_RE.search(line)
        if not m:
            continue
        try:
            d = dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        if today <= d <= cutoff:
            results.append({"date": d, "source": "Active Priorities", "text": line.strip("- ").strip()})
    return results


def _icloud_deadlines(days_ahead):
    results = []
    try:
        from icloud_calendar import ICloudCalDAV, DEFAULT_CALENDAR_NAME
        client = ICloudCalDAV()
        client.discover()
        today = dt.datetime.now()
        cutoff = today + dt.timedelta(days=days_ahead)
        for cal in client.calendars:
            if "VEVENT" not in cal.get("components", []):
                continue
            events = client.list_events(cal["url"], today, cutoff)
            for e in events:
                results.append({
                    "date": e["start"].date() if hasattr(e["start"], "date") else e["start"],
                    "source": f"iCloud/{cal['name']}",
                    "text": e.get("summary", "(untitled)"),
                })
    except Exception as e:
        results.append({"date": None, "source": "iCloud", "text": f"(couldn't reach iCloud Calendar: {e})"})
    return results


def get_unified_deadlines(days_ahead=14):
    items = _priorities_deadlines(days_ahead) + _icloud_deadlines(days_ahead)
    return sorted((i for i in items if i["date"]), key=lambda i: i["date"]) + [i for i in items if not i["date"]]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    days = 14
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    print(f"=== Deadlines in the next {days} days (Active Priorities + iCloud Calendar) ===")
    print("(Outlook and POLITEmall deadlines need a manual live-session check - no scheduled pull exists for either)\n")
    for item in get_unified_deadlines(days):
        date_str = item["date"].isoformat() if item["date"] else "?"
        print(f"[{date_str}] ({item['source']}) {item['text']}")
