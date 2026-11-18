# -*- coding: utf-8 -*-
"""Morning mail: one real letter from Loom to the owner, filed through the office door slot each morning.

    python morning_mail.py --print            compose and print the letter, files nothing
    python morning_mail.py --send             file ONE letter for today (refuses a second; skips when presence is 'asleep')
    python morning_mail.py --send --force     file it even if today's letter was already filed or presence is asleep

It never touches Discord. The letter carries today's weather, today's classes, exams in the next 14 days, assignments due in the
next 7, the protocols that are due now, and the top open cards on the Active Priorities board. Every source is wrapped: a source that
fails is left out with one short honest line ("weather unavailable"), never invented.

Reuses morning_briefing.py for weather and card parsing, and office_mail.load_store() for filing (sealing lives in office_mail_store).
Env overrides (for tests): ARCHIVE_MAIL_FILE (mail file), ARCHIVE_DATA_DIR or MORNING_MAIL_KV_DIR (tracker kv dir),
MORNING_MAIL_NOW (fixed local time, YYYY-MM-DDTHH:MM), MORNING_MAIL_STATE (state file), MORNING_MAIL_PRIORITIES (cards file),
MORNING_MAIL_PRESENCE (home|away|asleep), MORNING_MAIL_NO_WEATHER=1 (skip the weather call).
"""
import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import morning_briefing as mb  # noqa: E402

STATE_FILE = Path(r"J:\Jarvis2\logs\morning_mail_state.json")
EXAM_DAYS = 14
ASSIGNMENT_DAYS = 7
OVERDUE_LOOKBACK = 14

# ---------------------------------------------------------------------------------------------------------------------------
# Python port of frontend/protocolsDue.js dueList + the exam-readiness stage logic (frontend/examReadiness.js, protocolRegister.js).
# Kept honest by tests/test_morning_mail.py, which runs the same JSON cases through the real JS with node.
# ---------------------------------------------------------------------------------------------------------------------------
DEFAULT_SEMESTER_START = "2026-10-19"
KICKOFF_BEFORE_DAYS = 7
KICKOFF_AFTER_DAYS = 14
AUDIT_GAP_DAYS = 7
AUDIT_OVERDUE_DAYS = 14
MAX_ENTRIES = 400
STAGES = (("t7", "T-7", 7), ("t3", "T-3", 3), ("t1", "T-1", 1), ("t0", "T-0", 0))
LEVEL_RANK = {"overdue": 0, "due": 1, "info": 2}
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _str(v):
    return "" if v is None else str(v)


def one_line(text, mx):
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", _str(text))
    return re.sub(r"\s+", " ", t).strip()[:mx].rstrip()      # cut first, then strip: a cut at a space left a trailing space


def is_date(s):
    return bool(_DATE_RE.fullmatch(_str(s)))      # fullmatch: "$" also matched before a trailing newline


def _to_date(s):
    y, m, d = (int(x) for x in str(s).split("-"))
    return dt.date(y, m, d)


def is_real_date(s):
    if not is_date(s):
        return False
    try:
        d = _to_date(s)
    except ValueError:
        return False
    return 1900 <= d.year <= 2200


def days_between(a, b):
    """b - a in whole days; None when either is not a YYYY-MM-DD string."""
    if not is_date(a) or not is_date(b):
        return None
    try:
        return (_to_date(b) - _to_date(a)).days
    except ValueError:
        return None


def add_days(date, n):
    if not is_date(date):
        return None
    try:
        return (_to_date(date) + dt.timedelta(days=n)).isoformat()
    except (ValueError, OverflowError):
        return None


def weekday_of(date):
    """0 = Sunday ... 6 = Saturday, like JS getUTCDay."""
    if not is_date(date):
        return None
    try:
        return (_to_date(date).weekday() + 1) % 7
    except ValueError:
        return None


def clean_register(raw):
    if not isinstance(raw, list):
        return []
    seen, out = set(), []
    for e in raw:
        if not isinstance(e, dict):
            continue
        serial = one_line(e.get("serial"), 40)
        protocol = one_line(e.get("protocol"), 40)
        if not serial or not protocol or not is_date(e.get("date")) or serial in seen:
            continue
        seen.add(serial)
        out.append({"serial": serial, "protocol": protocol, "date": e["date"], "title": one_line(e.get("title"), 120),
                    "outcome": e.get("outcome") if e.get("outcome") in ("SIGNED", "DECLINED", "WAIVED") else "SIGNED",
                    "ref": one_line(e.get("ref"), 120)})
    return out[-MAX_ENTRIES:]


def entries_for(register, protocol=None, limit=50):
    allr = [e for e in reversed(clean_register(register)) if not protocol or e["protocol"] == protocol]
    return allr[:max(0, limit)]


def last_entry(register, protocol):
    got = entries_for(register, protocol, 1)
    return got[0] if got else None


def exam_key(e):
    e = e if isinstance(e, dict) else {}
    return f"{one_line(e.get('id'), 20)}:{one_line(e.get('name'), 40)}"


def exam_ref(exam, label):
    return f"exam:{exam_key(exam)}|{one_line(exam.get('date'), 10)}|{one_line(label, 6)}"


def _legacy_ref(exam):
    return f"{exam_key(exam)} {_str(exam.get('date'))}"[:120]


def _legacy_title(exam, label):
    return f"{one_line(exam.get('course'), 40)} {one_line(exam.get('name'), 40)} {label}"


def register_has_stage(register, exam, label):
    want, old, title = exam_ref(exam, label), _legacy_ref(exam), _legacy_title(exam, label)
    for e in entries_for(register, "exam-readiness", 500):
        if e["outcome"] != "DECLINED" and (e["ref"] == want or (e["ref"] == old and e["title"] == title)):
            return e
    return None


def _state_signed(state, key, sid):
    if not isinstance(state, dict):
        return False
    for k in list(state.keys())[:200]:
        if one_line(k, 70) != key:
            continue
        ex = state[k]
        if not isinstance(ex, dict):
            continue
        s = ex.get(sid)
        if isinstance(s, dict) and isinstance(s.get("signed"), dict) and one_line(s["signed"].get("serial"), 40):
            return True
    return False


def _exam_rows(exams, state, register, today):
    rows = []
    for e in exams:
        if not isinstance(e, dict) or not is_real_date(e.get("date")):
            continue
        days = days_between(today, e["date"])
        if days is None or days < 0:
            continue
        rows.append((days, e))
    rows.sort(key=lambda r: (r[0], _str(r[1].get("time"))))
    out = []
    for days, e in rows:
        key = exam_key(e)
        for idx, (sid, label, sdays) in enumerate(STAGES):
            if _state_signed(state, key, sid) or register_has_stage(register, e, label):
                continue
            next_started = idx < len(STAGES) - 1 and days <= STAGES[idx + 1][2]
            reached = days <= sdays
            status = "overdue" if next_started else "due" if reached else None
            if status:
                out.append({"key": key, "course": e.get("course"), "name": e.get("name"), "stage": label, "status": status, "days": days})
    return out


def due_list(register, facts=None):
    """-> [{id, protocol, tool, label, detail, level, count, data}] most urgent first; never throws on missing facts."""
    reg = clean_register(register)
    f = facts if isinstance(facts, dict) else {}
    today = f.get("today") if is_date(f.get("today")) else None
    if not today:
        return []
    wd = f.get("weekday")
    weekday = wd if isinstance(wd, int) and not isinstance(wd, bool) else weekday_of(today)
    out = []

    def push(item):
        out.append({"count": 1, "data": {}, **item})

    last_audit = last_entry(reg, "weekly-audit")
    since = days_between(last_audit["date"], today) if last_audit else None
    audit = {"id": "weekly-audit", "protocol": "weekly-audit", "tool": "weekly-audit", "label": "WEEKLY AUDIT"}
    if not last_audit:
        push({**audit, "detail": "NONE ON RECORD YET", "level": "due" if weekday == 5 else "info", "data": {"never": True}})
    elif since >= AUDIT_GAP_DAYS:
        push({**audit, "detail": f"LAST SIGNED {since} DAYS AGO ({last_audit['serial']})",
              "level": "overdue" if since >= AUDIT_OVERDUE_DAYS else "due", "data": {"since": since}})
    elif weekday == 5 and since > 0:
        push({**audit, "detail": f"FRIDAY AUDIT NOT SIGNED YET (LAST {last_audit['serial']})", "level": "due",
              "data": {"friday": True, "since": since}})

    start = f["semesterStart"] if is_date(f.get("semesterStart")) else DEFAULT_SEMESTER_START
    to_start = days_between(today, start)
    window_open = add_days(start, -KICKOFF_BEFORE_DAYS)
    signed_kickoff = any(e["outcome"] != "DECLINED" and window_open is not None and e["date"] >= window_open
                         for e in entries_for(reg, "semester-kickoff", 500))
    if to_start is not None and -KICKOFF_AFTER_DAYS <= to_start <= KICKOFF_BEFORE_DAYS and not signed_kickoff:
        if to_start > 0:
            detail = f"SEMESTER STARTS IN {to_start} DAY{'' if to_start == 1 else 'S'} ({start})"
        elif to_start == 0:
            detail = f"SEMESTER STARTS TODAY ({start})"
        else:
            detail = f"SEMESTER STARTED {-to_start} DAY{'' if to_start == -1 else 'S'} AGO ({start})"
        push({"id": "semester-kickoff", "protocol": "semester-kickoff", "tool": "semester-kickoff", "label": "SEMESTER KICKOFF",
              "detail": detail, "level": "overdue" if to_start <= 0 else "due", "data": {"to_start": to_start, "start": start}})

    exams = [e for e in (f.get("exams") if isinstance(f.get("exams"), list) else []) if isinstance(e, dict) and is_date(e.get("date"))]
    for d in _exam_rows(exams, f.get("examState"), reg, today):
        name = " ".join(x for x in (_str(d["course"]), _str(d["name"])) if x).strip() or "EXAM"
        missed = d["status"] == "overdue"
        left = "TODAY" if d["days"] == 0 else f"{d['days']} DAY{'' if d['days'] == 1 else 'S'} LEFT"
        push({"id": f"exam-readiness:{d['key']}:{d['stage']}", "protocol": "exam-readiness", "tool": "exam-readiness",
              "label": "EXAM READINESS", "detail": f"{name.upper()}: {d['stage']}{' MISSED' if missed else ''}, {left}",
              "level": "overdue" if missed or d["days"] <= 1 else "due",
              "data": {"name": name, "stage": d["stage"], "missed": missed, "days": d["days"]}})

    def count(n):
        try:
            v = float(n)
        except (TypeError, ValueError):
            return 0
        return int(v) if v == v and v != float("inf") and v > 0 else 0

    deb = count(f.get("debriefsPending"))
    if deb:
        push({"id": "op-debrief", "protocol": "op-debrief", "tool": "op-debrief", "label": "OPERATION DEBRIEF",
              "detail": f"{deb} OPERATION{'' if deb == 1 else 'S'} AWAITING A DEBRIEF", "level": "due", "count": deb})
    auth = count(f.get("authorizationsPending"))
    if auth:
        push({"id": "authorization", "protocol": "authorization", "tool": "authorization", "label": "OUTBOUND AUTHORIZATION",
              "detail": f"{auth} DRAFT{'' if auth == 1 else 'S'} WAITING FOR AUTHORIZATION", "level": "due", "count": auth})

    daily = f.get("dailyLast") if isinstance(f.get("dailyLast"), dict) else None
    if daily:
        for kind in ("opening", "closing"):
            if daily.get(kind) == today:
                continue
            push({"id": f"daily-{kind}", "protocol": "daily", "tool": "protocols", "label": f"{kind.upper()} PROTOCOL",
                  "detail": f"LAST DONE {daily[kind]}" if daily.get(kind) else "NOT DONE YET",
                  "level": "due" if kind == "opening" else "info"})

    return sorted(out, key=lambda x: LEVEL_RANK[x["level"]])  # sorted() is stable, like the JS sort with an index tiebreak


# ---------------------------------------------------------------------------------------------------------------------------
# data gathering (every source wrapped; None means "this source failed")
# ---------------------------------------------------------------------------------------------------------------------------
def kv_dir():
    return Path(os.environ.get("MORNING_MAIL_KV_DIR") or os.environ.get("ARCHIVE_DATA_DIR") or mb.KV_DIR)


def load_kv(name, default=None):
    """The kv value, `default` when the file does not exist; raises on a damaged file (so the source is reported unavailable)."""
    p = kv_dir() / f"{name}.json"
    if not p.is_file():
        return default
    return json.loads(p.read_text(encoding="utf-8"))


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def now_local():
    fixed = os.environ.get("MORNING_MAIL_NOW")
    return dt.datetime.fromisoformat(fixed) if fixed else dt.datetime.now()


def get_presence():
    forced = os.environ.get("MORNING_MAIL_PRESENCE")
    if forced:
        return forced
    import set_presence_mode
    return set_presence_mode.get_mode()


def _minutes(t):
    m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", _str(t))
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


def todays_classes(slots, today):
    wd = (today.weekday() + 1) % 7
    ok = [s for s in (slots if isinstance(slots, list) else [])
          if isinstance(s, dict) and s.get("day") == wd and _minutes(s.get("start")) is not None
          and _minutes(s.get("end")) is not None and _minutes(s["end"]) > _minutes(s["start"])]
    ok.sort(key=lambda s: (_minutes(s["start"]), _minutes(s["end"])))
    return [{"start": s["start"], "end": s["end"], "course": one_line(s.get("course"), 40), "room": one_line(s.get("room"), 30)}
            for s in ok]


def upcoming_exams(items, today, days=EXAM_DAYS):
    out = []
    for e in items if isinstance(items, list) else []:
        if not isinstance(e, dict) or not is_real_date(e.get("date")):
            continue
        delta = days_between(today.isoformat(), e["date"])
        if delta is not None and 0 <= delta <= days:
            label = " ".join(x for x in (one_line(e.get("course"), 40), one_line(e.get("name"), 80)) if x) or "exam"
            out.append({"days": delta, "date": e["date"], "time": one_line(e.get("time"), 5), "label": label})
    return sorted(out, key=lambda x: (x["days"], x["time"]))


def upcoming_assignments(items, today):
    out = []
    for a in items if isinstance(items, list) else []:
        if not isinstance(a, dict) or not is_real_date(a.get("due")):
            continue
        if _str(a.get("status")).lower() in ("done", "submitted", "complete", "completed"):
            continue
        delta = days_between(today.isoformat(), a["due"])
        if delta is None or delta > ASSIGNMENT_DAYS or delta < -OVERDUE_LOOKBACK:
            continue
        label = " ".join(x for x in (one_line(a.get("course"), 40), one_line(a.get("title") or a.get("name"), 80)) if x) or "assignment"
        out.append({"days": delta, "label": label})
    out.sort(key=lambda x: (x["days"], x["label"]))
    overdue = [x for x in out if x["days"] < 0][-3:]
    return overdue + [x for x in out if x["days"] >= 0]


def short_title(title, mx=60):
    """Card titles can run long ("X, banked 2026-08-20 (day after ...)"): keep the plain name, cut at a word."""
    t = re.split(r",\s*banked|\s\(|:\s", title)[0].strip() or title.strip()
    if len(t) > mx:
        t = t[:mx].rsplit(" ", 1)[0].rstrip(",;:-") + "..."
    return t


def top_cards(path, n=3):
    cards = mb.read_cards(path)
    open_cards = [c for c in cards if not c["done"]]
    needs = list(reversed([c for c in open_cards if c["needs_you"]]))  # new items go on top of the file, so the oldest are last
    rest = [c for c in open_cards if not c["needs_you"]]
    return {"open": len(open_cards), "top": [{"title": short_title(c["title"]), "needs_you": c["needs_you"]} for c in (needs + rest)[:n]]}


def gather(now=None):
    now = now or now_local()
    today = now.date()
    d = {"now": now, "today": today}
    d["weather"] = None if os.environ.get("MORNING_MAIL_NO_WEATHER") else _safe(mb.fetch_weather)
    timetable = _safe(load_kv, "tracker_timetable", [])
    d["timetable_count"] = len(timetable) if isinstance(timetable, list) else None
    d["classes"] = None if not isinstance(timetable, list) else todays_classes(timetable, today)      # a non-list timetable is unavailable, not "no classes"
    exams_raw = _safe(load_kv, "tracker_exams", [])
    d["exams"] = None if exams_raw is None else upcoming_exams(exams_raw, today)
    asg_raw = _safe(load_kv, "tracker_assignments", [])
    d["assignments"] = None if asg_raw is None else upcoming_assignments(asg_raw, today)
    register = _safe(load_kv, "protocol_register", [])
    start = _safe(load_kv, "semester_start", None)
    start = start.strip() if isinstance(start, str) and is_date(start.strip()) else DEFAULT_SEMESTER_START
    d["semester_start"] = start
    state = _safe(load_kv, "exam_readiness", {})
    if register is None:
        d["due"] = None
    else:
        d["due"] = _safe(due_list, register, {"today": today.isoformat(), "semesterStart": start,
                                              "exams": exams_raw if isinstance(exams_raw, list) else [], "examState": state})
    cards_path = Path(os.environ.get("MORNING_MAIL_PRIORITIES") or mb.PRIORITIES)
    d["cards"] = _safe(top_cards, cards_path)
    return d


# ---------------------------------------------------------------------------------------------------------------------------
# composition (pure)
# ---------------------------------------------------------------------------------------------------------------------------
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _when(days):
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days > 0:
        return f"in {days} days"
    return f"{-days} day{'' if days == -1 else 's'} overdue"


def _day_label(iso):
    d = _to_date(iso)
    return f"{WEEKDAYS[d.weekday()][:3]} {d.day} {d.strftime('%b')}"


def _protocol_line(it):
    data = it.get("data") or {}
    pid = it["id"]
    if pid == "weekly-audit":
        if data.get("never"):
            return "Weekly audit: none signed yet. Fridays are audit day."
        if data.get("friday"):
            return "Weekly audit: it is Friday and this week's is not signed yet."
        since = data.get("since")
        return f"Weekly audit: last signed {_plural(since, 'day')} ago" + (", properly overdue." if it["level"] == "overdue" else ".")
    if pid == "semester-kickoff":
        n = data.get("to_start")
        if n is None:
            return "Semester kickoff: not signed yet."
        if n > 0:
            return f"Semester kickoff: the semester starts in {_plural(n, 'day')}, kickoff not signed yet."
        if n == 0:
            return "Semester kickoff: the semester starts today, kickoff not signed yet."
        return f"Semester kickoff: the semester started {_plural(-n, 'day')} ago, kickoff still not signed."
    if it["protocol"] == "exam-readiness":
        left = "today" if data.get("days") == 0 else _plural(data.get("days"), "day") + " left"
        return f"Exam readiness: {data.get('name')} {data.get('stage')}" + (" (missed)" if data.get("missed") else "") + f", {left}."
    return f"{it['label'].capitalize()}: {it['detail'].lower()}."


def compose_letter(facts):
    """-> (subject, body). Pure: everything comes from `facts` (see gather()). A None source is reported unavailable, never invented."""
    today = facts["today"]
    wd = WEEKDAYS[today.weekday()]
    subject = f"Good morning, {wd} {today.day} {today.strftime('%B')}"
    lines = [f"morning the owner, it's {wd.lower()}. here is your day, as it stands.", ""]

    sem_start = facts.get("semester_start")
    if sem_start and today.isoformat() == sem_start:
        lines += ["Today is the first day of the new semester.", ""]

    lines.append("TODAY")
    w = facts.get("weather")
    lines.append(f"Weather: {w}." if w else "Weather unavailable.")
    lines.append("")

    lines.append("CLASSES")
    cls = facts.get("classes")
    if cls is None:
        lines.append("Classes unavailable (the timetable could not be read).")
    elif cls:
        for c in cls:
            lines.append(f"{c['start']} to {c['end']}  {c['course']}" + (f"  ({c['room']})" if c["room"] else ""))
    elif today.weekday() >= 5:
        lines.append(f"No classes today, it is a {wd}.")
    elif facts.get("timetable_count") == 0:
        lines.append("Your timetable is empty in the school tracker, so I cannot tell you today's classes.")
    else:
        lines.append("No classes on your timetable today.")
    lines.append("")

    lines.append("COMING UP")
    ex, asg = facts.get("exams"), facts.get("assignments")
    coming = []
    if ex is None:
        coming.append("Exams unavailable (the exam list could not be read).")
    for e in ex or []:
        t = f" at {e['time']}" if e["time"] else ""
        coming.append(f"Exam: {e['label']}, {_day_label(e['date'])}{t} ({_when(e['days'])}).")
    if asg is None:
        coming.append("Assignments unavailable (the assignment list could not be read).")
    for a in asg or []:
        coming.append(f"Due: {a['label']} ({_when(a['days'])}).")
    if ex == [] and asg == []:
        coming.append("No exams in the next 14 days and nothing due in the next 7.")
    elif ex == []:
        coming.append("No exams in the next 14 days.")
    lines += coming
    lines.append("")

    lines.append("DUE NOW")
    due = facts.get("due")
    if due is None:
        lines.append("Protocols unavailable (the register could not be read).")
    else:
        shown = [x for x in due if x["level"] != "info"]
        lines += [_protocol_line(x) for x in shown] if shown else ["No exam, audit or kickoff protocols due."]
    lines.append("")

    lines.append("ON YOUR BOARD")
    cards = facts.get("cards")
    if cards is None:
        lines.append("The priorities board is unavailable.")
    elif not cards["top"]:
        lines.append("Nothing open on the priorities board.")
    else:
        for c in cards["top"]:
            lines.append(f"- {c['title']}" + (" (waiting on you)" if c["needs_you"] else ""))
        more = cards["open"] - len(cards["top"])
        if more > 0:
            lines.append(f"...and {more} more open.")
    lines += ["", "that's the lot. i'm around if you want any of it handled.", "", "Loom"]
    return subject, "\n".join(lines)


# ---------------------------------------------------------------------------------------------------------------------------
# delivery
# ---------------------------------------------------------------------------------------------------------------------------
def state_file():
    return Path(os.environ.get("MORNING_MAIL_STATE") or STATE_FILE)


def read_state():
    try:
        s = json.loads(state_file().read_text(encoding="utf-8"))
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def write_state(date_iso, letter_id):
    p = state_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"last_sent": date_iso, "letter_id": letter_id}), encoding="utf-8")
    os.replace(tmp, p)


def send_letter(facts=None, force=False, presence_fn=None, now=None):
    """-> (status, detail) where status is 'sent', 'asleep', 'already' or 'error'."""
    now = now or now_local()
    today = now.date().isoformat()
    if not force:
        if (presence_fn or get_presence)() == "asleep":
            return "asleep", "presence is asleep: no letter filed"
        if read_state().get("last_sent") == today:
            return "already", f"today's letter ({read_state().get('letter_id', '?')}) was already filed; use --force to file another"
    claim = state_file().with_name("morning_mail.claim")
    try:
        claim.parent.mkdir(parents=True, exist_ok=True)
        if not force:
            if claim.exists() and time.time() - claim.stat().st_mtime < 300:
                return "already", "another run is filing today's letter right now"
        claim.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        import office_mail
        store = office_mail.load_store()
        if store is None:
            return "error", "office_mail_store.py was not found in the jarvis-ui repo"
        subject, body = compose_letter(facts or gather(now))
        sent = store.send({"from": "LOOM", "to": "THE_OWNER", "type": "NOTE", "subject": subject, "body": body}, wake=False)
    except Exception as e:
        return "error", f"could not file the letter: {e}"
    finally:
        try:
            claim.unlink()
        except OSError:
            pass
    try:
        write_state(today, sent["id"])
    except OSError as e:      # the letter is already sealed; failing here must not look like an error that triggers a second letter
        return "sent", f"letter {sent['id']} sealed but today's state could not be recorded ({e})"
    return "sent", f"letter {sent['id']} sealed ({sent['seal']['serial']}) and placed in the office for the owner"


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--print" in argv:
        subject, body = compose_letter(gather())
        print(f"SUBJECT: {subject}\n\n{body}")
        return 0
    if "--send" in argv:
        status, detail = send_letter(force="--force" in argv)
        print(detail)
        return 1 if status == "error" else 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
