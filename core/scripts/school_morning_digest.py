# -*- coding: utf-8 -*-
"""Daily 07:30 school digest: ONE short Discord DM to the owner with what is NEW at school since the last digest
(announcements, new course items, quizzes, e-learning, assignments) and what is due in the next 7 days.
Attendance quizzes and e-learning are flagged with '!!'. Read-only: no sign-in is attempted here (politemall_sync.py at 07:00
does that); the browser only opens the announcement pages. Costs no AI tokens. Scheduled task: JarvisSchoolMorningDigest.

    python school_morning_digest.py              # build and send
    python school_morning_digest.py --dry-run    # build and print, send nothing, change no state
    python school_morning_digest.py --skip-browser   # no announcement check (tests)
    python school_morning_digest.py --since 2026-10-19T00:00:00   # pretend the last digest was then (tests)

Sources: logs/politemall_sync_state.json ('seen' timestamps) + each course's _manifest.json (titles, due dates),
deadline_reminders.assignments() (due dates), logs/quiz_tasks.json (unfinished quizzes), and its OWN announcement scan
(logs/school_morning_seen.json, so it never steals what the other digests report). The first run records announcements silently."""
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbox_digest_common as C  # noqa: E402
import school_health as H  # noqa: E402

BASE = "https://rplms.polite.edu.sg"
SCHOOL = Path(r"J:\Jarvis-Brain\02 - School")
SYNC_STATE = C.LOGS / "politemall_sync_state.json"
QUIZ_FILE = C.LOGS / "quiz_tasks.json"
MY_STATE = C.LOGS / "school_morning_state.json"
MY_SEEN = C.LOGS / "school_morning_seen.json"
MAX_LINES = 14

NEWS_ITEMS = """
() => {
  const out = [];
  const walk = (root) => {
    root.querySelectorAll('a[href*="/d2l/le/news/"]').forEach((a) => out.push([a.getAttribute('href') || '', (a.innerText || a.getAttribute('title') || '').trim()]));
    root.querySelectorAll('*').forEach((e) => { if (e.shadowRoot) walk(e.shadowRoot); });
  };
  walk(document);
  return out;
}
"""


def flag(title):
    """'!! attendance quiz' / '!! e-learning' / 'quiz' / '' from a title."""
    t = (title or "").lower()
    if "attendance" in t:
        return "!! attendance quiz"
    if re.search(r"e-?\s?learning|elearning|async", t):
        return "!! e-learning"
    if "quiz" in t:
        return "quiz"
    return ""


def code_of(name):
    m = re.match(r"\s*([A-Z]\d{3})", name or "")
    return (m.group(1) if m else "?").lower()


def load_manifests():
    """{ou: {"code": 'a360', "topics": {id: entry}}} for every synced course folder (newest manifest per ou wins)."""
    out = {}
    for f in SCHOOL.glob("*(*S*)*/Course Files/_manifest.json"):
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        out[str(m.get("ou"))] = {"code": code_of(m.get("course")), "topics": {str(t.get("id")): t for t in m.get("topics", [])}}
    return out


def new_items(sync_state, manifests, quizzes, since, skip=()):
    """Lines for items first seen (or files updated) after `since`."""
    lines = []
    iso = since.isoformat(timespec="seconds")
    for key, ts in (sync_state.get("seen") or {}).items():
        if ts < iso or key in skip:  # skip: already sent as an instant alert by school_new_alert.py
            continue
        parts = key.split(":")
        if len(parts) == 3 and parts[1] in ("topic", "assign"):
            man = manifests.get(parts[0])
            ent = (man or {}).get("topics", {}).get(parts[2])
            if not man or not ent or ent.get("hidden"):
                continue
            kind = "assignment" if parts[1] == "assign" else (ent.get("type") or "item").lower().replace("contentservice", "content")
            due = f", due {ent['due'][:10]}" if ent.get("due") else ""
            fl = flag(ent.get("title")) or flag(ent.get("module"))
            lines.append((0 if fl.startswith("!!") else 1, f"{(fl + ': ') if fl else ''}{man['code']} {ent.get('title')} ({kind}{due})"))
        elif key in quizzes:
            lines.append((0 if flag(quizzes[key]).startswith("!!") else 1, f"quiz: {quizzes[key].lower()}"))
    for key, t in (sync_state.get("topics") or {}).items():  # files re-published with changes
        if (t.get("saved_at") or "") >= iso and ":assignfile:" not in key and key.count(":") == 1:
            ou, tid = key.split(":")
            if ((sync_state.get("seen") or {}).get(f"{ou}:topic:{tid}") or "") < iso and ou in manifests:
                lines.append((2, f"{manifests[ou]['code']} {t.get('title')} (file updated)"))
    lines.sort(key=lambda x: x[0])
    return [l for _, l in lines]


def due_soon(now, days=7):
    import deadline_reminders as D
    out = []
    for a in D.assignments():
        if now <= a["due"] <= now + timedelta(days=days):
            fl = flag(a["title"])
            out.append(f"{(fl + ': ') if fl else ''}{a['course'].lower()} {a['title'][:60]} due {a['due'].strftime('%a %d %b %H:%M').lower()}")
    return out


def scan_announcements(dry):
    """Returns (lines, error). Own seen file; the first ever run records silently. Opens the portal read-only, never signs in."""
    from playwright.sync_api import sync_playwright
    seen = C.load_json(MY_SEEN, {})
    first_run = not MY_SEEN.exists()
    lines, newseen = [], {k: list(v) for k, v in seen.items()} if isinstance(seen, dict) else {}
    with H.profile_lock(timeout=600):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(C.PROFILE_DIR, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(f"{BASE}/d2l/home", wait_until="domcontentloaded")
                _settle(page)
                if C.confirm_signed_out(page, f"{BASE}/d2l/home"):
                    return [], "school session is signed out (the 07:00 sync could not sign in)"
                import politemall_digest as P
                tiles = page.evaluate(P.COURSE_LINKS) or []
                newest = max((re.search(r"(\d\d)S(\d)", n or "").groups() for _, n in tiles if re.search(r"(\d\d)S(\d)", n or "")), default=None)
                failed = tried = 0
                for ou, name in tiles:
                    m = re.search(r"(\d\d)S(\d)", name or "")
                    if newest and (not m or m.groups() != newest):
                        continue  # current semester only
                    tried += 1
                    try:
                        page.goto(f"{BASE}/d2l/lms/news/main.d2l?ou={ou}", wait_until="domcontentloaded")
                        _settle(page)
                        if C.confirm_signed_out(page, page.url):
                            return [], "school session dropped while reading announcements"
                        items = page.evaluate(NEWS_ITEMS)
                    except Exception:
                        failed += 1
                        continue
                    ids, known = {}, set(newseen.get(str(ou), []))
                    for href, text in items:
                        mm = re.search(r"/d2l/le/news/\d+/(\d+)", href or "")
                        if mm and mm.group(1) not in ids:
                            ids[mm.group(1)] = text
                        elif mm and text and not ids[mm.group(1)]:
                            ids[mm.group(1)] = text
                    for nid, text in ids.items():
                        if nid not in known and not first_run and nid not in _alerted_ann(P.course_code(name, ou)):
                            fl = flag(text)
                            lines.append(f"{(fl + ': ') if fl else ''}{P.course_code(name, ou).lower()} announcement: {text[:70] or '(no title)'}")
                    newseen[str(ou)] = sorted(known | set(ids))
                if tried and failed == tried:
                    return [], "could not load any announcement page"
            finally:
                ctx.close()
    if not dry:
        C.atomic_write_json(MY_SEEN, newseen)
    return lines, None


def _alerted_ann(code):
    try:
        import politemall_poll as PP
        return PP._load_seen(code, "alerts")
    except Exception:
        return set()


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    page.wait_for_timeout(2500)


def build_message(new, ann, due, notes):
    out = []
    allnew = ann + new
    if allnew:
        out.append("school, new since yesterday:")
        out += [f"- {l}" for l in allnew[:MAX_LINES]]
        if len(allnew) > MAX_LINES:
            out.append(f"(+{len(allnew) - MAX_LINES} more in the course folders)")
    else:
        out.append("nothing new at school since yesterday")
    if due:
        out.append("due in the next 7 days:")
        out += [f"- {l}" for l in due[:8]]
    out += notes
    return "\n".join(out)


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    now = datetime.now().astimezone()
    st = C.load_json(MY_STATE, {})
    if "--since" in argv:
        since = datetime.fromisoformat(argv[argv.index("--since") + 1])
    else:
        try:
            since = datetime.fromisoformat(st["last_run"])
        except (KeyError, ValueError):
            since = datetime.now() - timedelta(hours=24)
    since = since.replace(tzinfo=None)
    notes, ann, ann_err = [], [], None
    if "--skip-browser" not in argv:
        try:
            ann, ann_err = scan_announcements(dry)
        except H.LockBusy:
            ann_err = "browser profile busy"
        except Exception as e:
            ann_err = f"announcement check crashed ({type(e).__name__})"
    if ann_err:
        notes.append(f"couldn't check announcements: {ann_err}")
        if "busy" not in ann_err:
            H.report("school_morning_digest", False, f"morning school digest could not read announcements: {ann_err}", dry=dry)
    elif "--skip-browser" not in argv:
        H.report("school_morning_digest", True, dry=dry)
    sync_ok = H.last_ok("politemall_sync")
    if not sync_ok or datetime.now() - sync_ok > timedelta(hours=14):
        notes.append("heads up: the course file sync has not worked for " + (f"{int((datetime.now() - sync_ok).total_seconds() // 3600)}h" if sync_ok else "a while") + ", new files may be missing")
    quizzes = {q["id"]: f"{q.get('course')}: {q.get('title')}" for q in (C.load_json(QUIZ_FILE, {}) or {}).get("quizzes", [])}
    new = new_items(C.load_json(SYNC_STATE, {}), load_manifests(), quizzes, since, set(C.load_json(C.LOGS / "school_alert_state.json", {}).get("keys", [])))
    due = due_soon(datetime.now(__import__("deadline_reminders").SGT))
    msg = build_message(new, ann, due, notes)
    print(msg)
    if dry:
        print("(dry run: not sent, no state changed)")
        return 0
    if not H._send(msg):
        print("send failed; state not advanced", file=sys.stderr)
        return 2
    st["last_run"] = datetime.now().isoformat(timespec="seconds")
    C.atomic_write_json(MY_STATE, st)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(C.LOGS / "school_morning_digest.log", "a", encoding="utf-8")
        print(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===")
    try:
        sys.exit(main())
    except Exception as e:
        print(f"school morning digest crashed: {type(e).__name__}: {e}", file=sys.stderr)
        H.report("school_morning_digest", False, f"morning school digest crashed ({type(e).__name__})")
        sys.exit(1)
