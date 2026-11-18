# -*- coding: utf-8 -*-
"""Immediate new-item alerts for school: every 2 hours (07:00-21:00, task JarvisSchoolNewAlert) it refreshes the read-only course
sync, checks announcements, and sends ONE short Discord DM per NEW quiz / e-learning / announcement, with the course, the due date
POLITEmall shows (and a note that it may not be the real one) and whether it is an attendance quiz, limited attempts or unlimited tries.
Read-only: never signs in by itself beyond what politemall_sync.py already does, never clicks or submits anything. No AI tokens.

    python school_new_alert.py              # sync, check, send
    python school_new_alert.py --dry-run    # print what would be sent, change no state, send nothing
    python school_new_alert.py --no-sync    # skip the sync step (uses the state the last sync wrote)

De-duplication: announcements through politemall_poll (category 'alerts', one state file per course); sync items through
logs/school_alert_state.json. The first run of each only records what exists (silent baseline). Also runs deadline_reminders.py --notify.
Failures go to school_health.report (one Discord DM per 6 h, one recovery DM)."""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbox_digest_common as C  # noqa: E402
import school_health as H  # noqa: E402
import school_morning_digest as M  # noqa: E402

STATE = C.LOGS / "school_alert_state.json"
NOTE = "shown due date may not be the real one"
CAP = 8


def norm(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def interesting(title, module="", typ=""):
    """Kind of item worth an instant alert, or ''. Quizzes, e-learning, async work."""
    f = M.flag(title) or M.flag(module)
    if f:
        return f
    return ""


def tries_text(attempts):
    a = (attempts or "").strip()
    if not a or a == "?":
        return "tries: unknown (check on politemall)"
    if re.search(r"unlimited", a, re.I):
        return "unlimited tries"
    m = re.search(r"/\s*(\d+)", a)
    return f"limited attempts ({m.group(1)} allowed)" if m else f"attempts {a}"


def sync_candidates(sync_state, manifests, quizzes, quiz_details):
    """[(key, norm_title, message)] for every sync item that is a quiz / e-learning. `quizzes` = {id: 'course: title'}."""
    out = []
    by_title = {norm(q.get("title")): q for q in quiz_details}
    for key in (sync_state.get("seen") or {}):
        parts = key.split(":")
        if len(parts) == 3 and parts[1] == "topic":
            man = manifests.get(parts[0])
            ent = (man or {}).get("topics", {}).get(parts[2])
            if not ent or ent.get("hidden"):
                continue
            fl = interesting(ent.get("title"), ent.get("module"), ent.get("type"))
            if not fl:
                continue
            q = by_title.get(norm(ent.get("title")))
            out.append((key, norm(ent.get("title")), format_item(
                "e-learning" if "e-learning" in fl else "quiz", man["code"], ent.get("title"), fl, q)))
        elif key in quizzes:
            q = next((x for x in quiz_details if x.get("id") == key), None) or {}
            title = q.get("title") or quizzes[key]
            code = M.code_of(q.get("course") or quizzes[key])
            out.append((key, norm(title), format_item("quiz", code, title, M.flag(title) or "quiz", q)))
    return out


def format_item(kind, code, title, fl, q):
    lines = [f"new {kind} [{code}]: {title}"]
    tags = []
    if "attendance" in fl:
        tags.append("!! attendance quiz")
    elif fl.startswith("!!"):
        tags.append(fl)
    tags.append(tries_text((q or {}).get("attempts")))
    lines.append(", ".join(tags))
    due = ((q or {}).get("due") or "").strip()
    lines.append(f"shown due: {due.lower() if due else 'none shown'} ({NOTE})")
    return "\n".join(lines)


def scan_announcements(first_codes_ok=True):
    """Returns (items, error): items = [(code, ou, nid, title)] for the current semester. Read-only, never signs in."""
    from playwright.sync_api import sync_playwright
    import politemall_digest as P
    items = []
    with H.profile_lock(timeout=600):
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(C.PROFILE_DIR, headless=True)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(f"{M.BASE}/d2l/home", wait_until="domcontentloaded")
                M._settle(page)
                if C.confirm_signed_out(page, f"{M.BASE}/d2l/home"):
                    return [], "school session is signed out"
                tiles = page.evaluate(P.COURSE_LINKS) or []
                sems = [re.search(r"(\d\d)S(\d)", n or "").groups() for _, n in tiles if re.search(r"(\d\d)S(\d)", n or "")]
                newest = max(sems, default=None)
                tried = failed = 0
                for ou, name in tiles:
                    m = re.search(r"(\d\d)S(\d)", name or "")
                    if newest and (not m or m.groups() != newest):
                        continue
                    tried += 1
                    try:
                        page.goto(f"{M.BASE}/d2l/lms/news/main.d2l?ou={ou}", wait_until="domcontentloaded")
                        M._settle(page)
                        if C.confirm_signed_out(page, page.url):
                            return [], "school session dropped while reading announcements"
                        raw = page.evaluate(M.NEWS_ITEMS)
                    except Exception:
                        failed += 1
                        continue
                    seen = {}
                    for href, text in raw:
                        mm = re.search(r"/d2l/le/news/\d+/(\d+)", href or "")
                        if mm and (mm.group(1) not in seen or (text and not seen[mm.group(1)])):
                            seen[mm.group(1)] = text
                    code = P.course_code(name, ou)
                    items += [(code, ou, nid, t) for nid, t in seen.items()]
                if tried and failed == tried:
                    return [], "could not load any announcement page"
            finally:
                ctx.close()
    return items, None


def send(msg, dry):
    return H._send(msg, dry)


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    import politemall_poll as PP
    if "--no-sync" not in argv:
        r = subprocess.run([sys.executable, str(HERE / "politemall_sync.py")], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
        print("sync exit", r.returncode)
        if r.returncode == 1:  # sign-in failed: the sync already told H.report; nothing new can be read
            print("sync could not sign in; skipping the new-item check")
            return 1
    st = C.load_json(STATE, {})
    first = not st.get("baselined")
    alerted, titles = set(st.get("keys", [])), set(st.get("titles", []))
    quiz_file = C.load_json(M.QUIZ_FILE, {}) or {}
    quiz_details = quiz_file.get("quizzes", [])
    quizzes = {q["id"]: f"{q.get('course')}: {q.get('title')}" for q in quiz_details}
    cands = sync_candidates(C.load_json(M.SYNC_STATE, {}), M.load_manifests(), quizzes, quiz_details)
    msgs, new_keys = [], []
    for key, nt, msg in cands:
        if key in alerted:
            continue
        new_keys.append(key)
        if first or nt in titles:  # baseline run, or the same quiz already announced under another id
            titles.add(nt)
            continue
        titles.add(nt)
        msgs.append(msg)
    # announcements
    ann_new, ann_err = [], None
    try:
        items, ann_err = scan_announcements()
        if ann_err and ("signed out" in ann_err or "dropped" in ann_err) and not dry:
            # 2026-10-27 (the owner: this should always fix itself): sign in again unattended (school_health limits failed attempts to one per 2 h), then read once more
            try:
                with H.profile_lock(timeout=600):
                    relogged = H.relogin("announcement scan found the session signed out")
                if relogged:
                    items, ann_err = scan_announcements()
            except H.LockBusy:
                pass
    except H.LockBusy:
        items, ann_err = [], "busy"
    except Exception as e:
        items, ann_err = [], f"announcement check crashed ({type(e).__name__})"
    ann_marks = {}
    if not ann_err:
        for code, ou, nid, title in items:
            known_file = PP._state_path(code, "alerts").exists()
            if PP.diff_new(code, [{"nid": nid, "title": title}], category="alerts"):
                ann_marks.setdefault(code, []).append({"nid": nid, "title": title})
                if known_file:  # a course with no state file yet is a silent baseline
                    fl = M.flag(title)
                    ann_new.append(f"new announcement [{code.lower()}]: {title[:100] or '(no title)'}\n"
                                   f"{(fl + ', ') if fl else ''}no due date shown ({NOTE}); open it to check the dates inside")
    elif ann_err != "busy":
        H.report("school_new_alert", False, f"new-item check could not read announcements: {ann_err}", dry=dry)
    if not ann_err:
        H.report("school_new_alert", True, dry=dry)
    msgs += ann_new
    if first and not dry:
        print(f"first run: baselined {len(new_keys)} existing item(s), nothing sent")
    sent_ok = True
    for m in msgs[:CAP]:
        print(m, "\n")
        sent_ok = send(m, dry) and sent_ok
    if len(msgs) > CAP:
        sent_ok = send(f"(+{len(msgs) - CAP} more new school items, see the course folders) ({NOTE})", dry) and sent_ok
    if not msgs:
        print("nothing new to alert")
    if not dry:
        if sent_ok:  # only mark as done when the DMs really went out
            for code, its in ann_marks.items():
                PP.mark_seen(code, its, category="alerts")
            st.update(baselined=True, keys=sorted(alerted | set(new_keys)), titles=sorted(titles), last_run=datetime.now().isoformat(timespec="seconds"))
            C.atomic_write_json(STATE, st)
            subprocess.run([sys.executable, str(HERE / "deadline_reminders.py"), "--notify"], capture_output=True, timeout=120)
        else:
            print("a send failed; nothing marked, will retry next run", file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(C.LOGS / "school_new_alert.log", "a", encoding="utf-8")
        print(f"\n=== {datetime.now().isoformat(timespec='seconds')} ===")
    try:
        sys.exit(main())
    except Exception as e:
        print(f"school new alert crashed: {type(e).__name__}: {e}", file=sys.stderr)
        H.report("school_new_alert", False, f"new-item check crashed ({type(e).__name__})")
        sys.exit(1)
