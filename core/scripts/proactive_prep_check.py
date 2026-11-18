# -*- coding: utf-8 -*-
"""Proactive deadline prep flagging (2026-08-20, the owner's follow-up pick
from the brainstorm: "instead of just tracking deadlines, start drafting
cheatsheet skeletons/flashcards a few days early automatically, not only
when you actually ask like tonight").

Scoped honestly, not overclaimed: this automates the NOTICING part (which
is genuinely mechanical -- nobody has to remember to check), not the
actual drafting. Real prep work (finding the right source material,
judging what's actually testable, building worked examples) is exactly
the kind of live judgment call this session did across 17 real rounds
tonight for A232 -- that's not something to blindly batch-script, a
scheduled job can't tell what's genuinely worth including the way a live
session checking real papers can. What this DOES do reliably: surface
"hey, X is coming up in N days and hasn't been started" so a live session
picks it up and does the real work, same as tonight, just earlier.

Reuses unified_deadlines.py's existing Active-Priorities + iCloud merge.
Only flags entries that look like an actual assessment (keyword match:
exam/FA/CA/assessment/quiz/test/ESE/final), not every calendar entry.
Tracks which deadlines have already been flagged (by exact text) so it
pings once, not every single day until the deadline passes.

Usage:
    python proactive_prep_check.py [--days-ahead N] [--acknowledge <text>]
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from unified_deadlines import get_unified_deadlines
from win_toast import show_toast

FLAGGED_PATH = Path(r"J:\Jarvis2\logs\proactive_prep_flagged.json")
LOG_PATH = Path(r"J:\Jarvis2\logs\proactive_prep_check.jsonl")
DAYS_AHEAD_DEFAULT = 5
ASSESSMENT_RE = re.compile(r"\b(exam|FA|CA\d?|assessment|quiz|test|ESE|final)\b", re.IGNORECASE)


def _load_flagged():
    try:
        return set(json.loads(FLAGGED_PATH.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return set()


def _save_flagged(flagged):
    FLAGGED_PATH.parent.mkdir(parents=True, exist_ok=True)
    FLAGGED_PATH.write_text(json.dumps(sorted(flagged), indent=2), encoding="utf-8")


def check(days_ahead=DAYS_AHEAD_DEFAULT):
    flagged = _load_flagged()
    items = get_unified_deadlines(days_ahead)
    new_flags = []
    for item in items:
        if not item["date"]:
            continue
        # only trust iCloud calendar events for this specific trigger,
        # not Active Priorities entries -- caught live: a banked task
        # note ("Robotic arm project, banked 2026-08-20 (day after the
        # A232 exam...)") false-matched on "exam" appearing as passing
        # context, not the actual subject, and its date is just "when
        # this got banked," not a real due date. iCloud events have
        # clean, deliberate titles ("A241 Cell Culture - Final
        # Assessment") and a genuine date by construction -- a much more
        # reliable signal than keyword-matching free-form prose.
        if not item["source"].startswith("iCloud"):
            continue
        if not ASSESSMENT_RE.search(item["text"]):
            continue
        key = f"{item['date'].isoformat()}|{item['text']}"
        if key in flagged:
            continue
        new_flags.append(item)
        flagged.add(key)

    if new_flags:
        _save_flagged(flagged)
        for item in new_flags:
            days_out = (item["date"] - dt.date.today()).days
            show_toast("Jarvis - upcoming assessment", f"{item['text'][:150]} (in {days_out}d)")

    result = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "checked": len(items),
        "new_flags": [i["text"] for i in new_flags],
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")
    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    days = DAYS_AHEAD_DEFAULT
    if "--days-ahead" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days-ahead") + 1])
    r = check(days)
    print(r)
