# -*- coding: utf-8 -*-
"""School sensor: third sensor of the redesigned harness (2026-10-30, step B of the Harness Rebuild Instructions).

What he repeatedly had to ask about in his last 3 days of messages included the timetable and school alerts, and the existing school jobs only fire on a
fixed schedule. This notices two facts and raises an intent for each so the wake brief and the stop hook surface them:
  1. a lesson starting within the next 90 minutes (from logs/timetable.json, the 114 lessons read off his SNAPP2 screenshots);
  2. unread POLITEmall announcements (from logs/politemall_digest.json, which politemall_poll.py keeps current).
It perceives only: it never sends, and it never opens the portal. Whether to tell him is my judgement at wake time (and the existing school_new_alert.py already
messages him about new announcements, so an intent for those is closed as skipped when he has already been told).
    python sense_school.py [--dry-run]
Kill switch: logs/sense_school_off.txt. Pure core (unit tested): class_soon(events, now_epoch, minutes), unread(digest), due_soon(items, now_epoch).
Note: the timetable holds lessons only (no exam or assessment entries), so no exam logic is faked here; exams belong to a later source."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = r"J:\Jarvis2\logs"
OFF = "sense_school_off.txt"
TIMETABLE = "timetable.json"
DIGEST = "politemall_digest.json"
SOON_MIN = 90
STATE = "sense_school_state.json"
UNREAD_REPEAT_S = 24 * 3600


def repeat_unread(state, what, now, window=UNREAD_REPEAT_S):
    """Pure. True when this exact set of unread announcements (same text: count and courses) was already seen less than `window` seconds ago. The portal's unread flag only
    clears when he opens the items there, so the same two items kept coming back every six hours (the dedupe cooldown) and each time I closed the intent as skipped because he
    had already been told by DM (2026-10-30: three times in one day). An unchanged set is reported as a quiet repeat (event value 0, no intent) for a day, then speaks again."""
    return bool(state) and state.get("unread") == what and now - float(state.get("at") or 0) < window


def _epoch(date_str, hhmm):
    try:
        return time.mktime(time.strptime("%s %s" % (date_str, hhmm), "%Y-%m-%d %H:%M"))
    except (ValueError, TypeError):
        return None


def class_soon(events, now_epoch, minutes=SOON_MIN):
    """Pure. -> [(what, value, dedupe, not_before, deadline)] for each lesson that starts within `minutes` from now (and not yet started).
    not_before is when the heads-up becomes useful (the window opens), deadline is the lesson start."""
    out = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        start = _epoch(e.get("date"), e.get("start"))
        if start is None or not (0 < start - now_epoch <= minutes * 60):
            continue
        mins = int((start - now_epoch) // 60)
        venue = e.get("venue") or "venue not listed"
        mode = e.get("mode") or ""
        what = "%s starts in %d min (%s-%s, %s, %s)" % (e.get("title") or e.get("module_code") or "a lesson", mins, e.get("start"), e.get("end"), venue, mode)
        dedupe = "class-%s-%s-%s" % (e.get("date"), e.get("start"), e.get("module_code") or "x")
        out.append((what, 3, dedupe, time.strftime("%Y-%m-%d %H:%M", time.localtime(start - minutes * 60)), time.strftime("%Y-%m-%d %H:%M", time.localtime(start))))
    return sorted(out, key=lambda t: t[4])


def unread(digest):
    """Pure. -> (what, value, dedupe) or None. Unread POLITEmall announcements across his courses; the digest timestamp keys the dedupe so a new poll with the
    same unread count on a later day is still seen once per day."""
    if not isinstance(digest, dict):
        return None
    try:
        total = int(digest.get("unread") or 0)
    except (TypeError, ValueError):
        return None
    if total <= 0:
        return None
    courses = sorted(c for c, n in (digest.get("courses") or {}).items() if isinstance(n, int) and n > 0)
    day = (digest.get("ts") or "")[:10] or "unknown-day"
    return ("%d unread POLITEmall announcement(s)%s" % (total, (" in " + ", ".join(courses)) if courses else ""), 3, "politemall-unread-%s-%d" % (day, total))


def due_soon(items, now_epoch):
    """Pure. items: [{course, title, due (epoch seconds)}]. -> [(what, value, dedupe, not_before, deadline)] for assignments or quizzes due within 36 hours (one
    heads-up) and again within 6 hours (the last call). Nothing already overdue. The 07:30 digest lists the next 7 days once a day; this is the timely nudge a friend
    would give the evening before and the morning of (2026-10-30, his goal: remind me of things I missed, without being asked)."""
    out = []
    for it in items or []:
        try:
            due, course, title = float(it["due"]), str(it.get("course", "")), str(it.get("title", ""))
        except (KeyError, TypeError, ValueError):
            continue
        left = due - now_epoch
        if not 0 < left <= 36 * 3600:
            continue
        phase = "6h" if left <= 6 * 3600 else "36h"
        when = time.strftime("%a %d %b %H:%M", time.localtime(due)).lower()
        hours = max(1, int(left // 3600))
        what = "%s '%s' is due %s (in about %d h). Remind him in one casual line unless he already has it done." % (course.lower(), title[:60], when, hours)
        out.append((what, 4, "school-due-%s-%s-%s" % (course, title[:30], phase), None, due))
    return out


def _assignments():
    """Impure. The assignments the school digest already reads, as epoch dicts; [] on any failure."""
    try:
        import deadline_reminders as D
        return [{"course": a["course"], "title": a["title"], "due": a["due"].timestamp()} for a in D.assignments()]
    except Exception:
        return []


def _load(name):
    try:
        return json.load(open(os.path.join(LOGS, name), encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    argv = argv or []
    if os.path.exists(os.path.join(LOGS, OFF)):
        print("sense_school: off")
        return 0
    tt = _load(TIMETABLE) or {}
    found = [("class", w, v, d, nb, dl) for (w, v, d, nb, dl) in class_soon(tt.get("events"), time.time())]
    u = unread(_load(DIGEST))
    if u:
        found.append(("unread_announcements", u[0], u[1], u[2], None, None))
    found += [("due_soon", w, v, d, nb, dl) for (w, v, d, nb, dl) in due_soon(_assignments(), time.time())]
    if "--dry-run" in argv:
        for kind, what, value, dedupe, nb, dl in found:
            print("would raise v%d %-22s %s" % (value, kind, what))
        print("%d finding(s), nothing written" % len(found))
        return 0
    import intent_store
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    intent_store.expire_leases(intents)
    added = 0
    state = _load(STATE) or {}
    now = time.time()
    for kind, what, value, dedupe, nb, dl in found:
        if kind == "unread_announcements":
            if repeat_unread(state, what, now):
                intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(), "source": "school", "kind": kind, "data": {"what": what, "value": 0, "repeat": True}})
                continue
            state = {"unread": what, "at": now}
            try:
                with open(os.path.join(LOGS, STATE), "w", encoding="utf-8") as f:
                    json.dump(state, f)
            except OSError:
                pass
        intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(), "source": "school", "kind": kind, "data": {"what": what, "value": value}})
        cand = intent_store.new_intent("school_" + kind, what, tier=1, value=value, dedupe=dedupe, deadline=dl, not_before=nb, from_event=kind)
        if intent_store.is_duplicate(intents, cand):
            continue
        intents.append(cand)
        added += 1
    if added:
        intent_store.write_intents(intents)
    print("sense_school: %d finding(s), %d new intent(s)" % (len(found), added))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
