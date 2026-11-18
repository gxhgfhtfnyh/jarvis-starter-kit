# -*- coding: utf-8 -*-
"""
State-tracking half of the POLITEmall announcement auto-poll. The actual page
reads happen in a live Playwright MCP session (no way to shell out to a live
browser tool from a plain script) - this just tracks what's already been seen
per course so re-running the check only surfaces genuinely new items.

Live-session workflow (see Jarvis Capabilities.md for the full method):
  1. For each course org unit ID, navigate to
     https://rplms.polite.edu.sg/d2l/lms/news/main.d2l?ou=<id>
  2. Extract every `a[href*="/d2l/le/news/"]` - text is the title, the numeric
     segment in the href (.../news/<ou>/<nid>/view) is a stable unique ID.
  3. Pass the list to diff_new() below to find what's actually new.
  4. Call mark_seen() to record them so the next run doesn't re-flag them.

Usage (as a library, from a live session):
    from politemall_poll import diff_new, mark_seen
    new_items = diff_new("A364", [{"nid": "1072940", "title": "..."}, ...])
    mark_seen("A364", [{"nid": "1072940", "title": "..."}, ...])

    # Item 25 (2026-08-09 tools brainstorm): the same diff/mark_seen pair
    # tracks course *resources*, not just announcements - pass category=
    # "resources" so resource IDs are tracked in a separate state file and
    # can never collide with an announcement ID:
    new_files = diff_new("A364", [{"nid": "...", "title": "..."}], category="resources")
    mark_seen("A364", [{"nid": "...", "title": "..."}], category="resources")
"""
import datetime as dt
import json
from pathlib import Path

STATE_DIR = Path(r"J:\Jarvis2\politemall_state")
STATE_DIR.mkdir(exist_ok=True)

# Course name -> D2L org unit ID, confirmed live 2026-08-08 from the homepage
# course tiles. Update if a course's org unit ID ever changes (new semester).
# SEMESTER CHANGE found 2026-11-02: the 8 courses above were 26S1's; none of them are the real
# current roster any more (that's exactly why A210 sat unpolled -- it was never in this dict).
# Re-discovered live from the real homepage tiles (26S2, confirmed via jarvis_capabilities
# "one of six current-semester courses" cross-check): A210, A322, A330, A336, A360, G134. Keeping
# the old 8 below rather than deleting them -- their state files are historical record, not live.
COURSES = {
    "A210": "984846",
    "A322": "984673",
    "A330": "984965",
    "A336": "984966",
    "A360": "984954",
    "G134": "984983",
}
OLD_COURSES_26S1 = {
    "A244": "865718",
    "A245": "865719",
    "A364": "865464",
    "G133": "865730",
    "A241": "865460",
    "A232": "865458",
    "A231": "865459",
    "G135": "865732",
}


def _state_path(course, category=""):
    # category="" keeps the original announcements filename for backward
    # compatibility with state files already written before item 25 (2026-08-09)
    # added category support (e.g. "resources", tracked separately so a
    # resource's ID can never collide with an announcement's).
    suffix = f"_{category}" if category else ""
    return STATE_DIR / f"{course}{suffix}.json"


def _load_seen(course, category=""):
    path = _state_path(course, category)
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


# 2026-11-06: generalized -- None when no avatar app is configured (politemall_config.py), in which
# case the nudge below is just skipped (see diff_new()'s guard on AVATAR_NUDGE_FLAG being falsy).
import politemall_config as _cfg
AVATAR_NUDGE_FLAG = Path(_cfg.AVATAR_NUDGE_FLAG) if _cfg.AVATAR_NUDGE_FLAG else None


def diff_new(course, items, category=""):
    """items: list of {"nid": str, "title": str}. Returns the subset never seen before."""
    seen = _load_seen(course, category)
    new_items = [it for it in items if it["nid"] not in seen]
    if new_items and AVATAR_NUDGE_FLAG:
        # Best-effort nudge for the avatar's environmental-signal poll -
        # this only runs inside a live Playwright session (no standalone
        # cron access to the actual page), so this is the one place that
        # genuinely knows "new announcement found" happened just now.
        # None (no avatar app configured) is handled by this same guard, not just OSError below.
        try:
            AVATAR_NUDGE_FLAG.write_text(
                json.dumps({"course": course, "category": category or "announcements", "count": len(new_items), "checked": dt.datetime.now().isoformat()}),
                encoding="utf-8",
            )
        except OSError:
            pass
    return new_items


def mark_seen(course, items, category=""):
    seen = _load_seen(course, category)
    seen.update(it["nid"] for it in items)
    _state_path(course, category).write_text(json.dumps(sorted(seen)), encoding="utf-8")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for course in COURSES:
        seen = _load_seen(course)
        print(f"{course}: {len(seen)} announcements tracked" if seen else f"{course}: not yet polled")
