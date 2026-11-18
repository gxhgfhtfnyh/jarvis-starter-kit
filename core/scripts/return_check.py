# -*- coding: utf-8 -*-
"""One-command return check: runs every routine check and prints a short summary (about 12 lines) so it costs one read
instead of six tool calls. Read-only except that the POLITEmall sync downloads new course files.

    python return_check.py            # full check
    python return_check.py --quick    # skip the browser sources (Outlook, POLITEmall); Gmail, Telegram, weather, quizzes only

Sources: Gmail (API), Outlook (headless badge read), POLITEmall (announcements + course sync, new files/assignments), Teams (digest),
quizzes (from the last scan), Telegram (the daemon's snapshot, never a second connection), weather.
Microsoft Teams: teams_digest.py (unread count; when above 0 it also lists the newest activity, which marks them read in Teams).
Bank alerts (DBS/POSB) are only counted here; run dbs_transaction_summarizer.py on them per the DBS ledger rules.
Never prints message bodies, only counts, senders and subjects."""
import json
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
LOGS = HERE.parent / "logs"
sys.path.insert(0, str(HERE))


def load(name, default=None):
    try:
        return json.loads((LOGS / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def run_script(name, *args, timeout=420):
    try:
        r = subprocess.run([sys.executable, str(HERE / name), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 99, f"{type(e).__name__}: {e}"


def gmail():
    try:
        from gmail_api import get_service, list_unread
        total = get_service().users().labels().get(userId="me", id="INBOX").execute().get("messagesUnread")
        msgs = list_unread(max_results=10)
    except Exception as e:
        return f"Gmail: could not check ({type(e).__name__}: {str(e)[:80]})"
    bank = [m for m in msgs if re.search(r"dbs|posb|paynow|paylah", (m.get("from") or "") + (m.get("subject") or ""), re.I)]
    out = f"Gmail: {total} unread" + (f", {len(bank)} bank alert(s) in the newest 10: run dbs_transaction_summarizer.py" if bank else "")
    for m in msgs[:5]:
        frm = re.sub(r"<.*?>", "", m.get("from") or "").strip().strip('"')[:28]
        out += f"\n    - {frm}: {(m.get('subject') or '')[:60]}"
    return out + (f"\n    (+{len(msgs) - 5} more of the newest 10)" if len(msgs) > 5 else "")


def telegram():
    try:
        from unified_inbox import telegram_digest
        chats = telegram_digest()
    except Exception as e:
        return f"Telegram: could not read ({type(e).__name__})"
    if chats is None:
        return "Telegram: no snapshot (daemon not running?)"
    if chats and "error" in chats[0]:
        return f"Telegram: snapshot unreadable ({chats[0]['error'][:60]})"
    if not chats:
        return "Telegram: nothing unread"
    parts = [f"{c.get('name') or c.get('title') or '?'} ({c.get('unread_count')})" for c in chats[:8]]
    return f"Telegram: {len(chats)} chat(s) with unread: " + ", ".join(parts) + " (ask before summarising personal chats)"


def weather():
    try:
        import weather_check
        return "Weather: " + weather_check.summary_line()
    except Exception as e:
        return f"Weather: could not fetch ({type(e).__name__})"


def browser_sources(quick):
    if quick:
        return ["Outlook / POLITEmall / Teams: skipped (--quick)"]
    lines = []
    code, out = run_script("outlook_digest.py", timeout=200)
    d = load("outlook_digest.json", {})
    stamp_ok = d.get("ts", "") >= (datetime.now() - timedelta(minutes=10)).isoformat(timespec="seconds")
    if code == 0 and stamp_ok:
        lines.append(f"Outlook: {d.get('unread')} unread in the inbox badge ({d.get('note', '')})")
    else:
        lines.append(f"Outlook: check FAILED ({out.strip()[-120:] or 'no output'}); last known {d.get('unread')} at {d.get('ts')}")
    code, out = run_script("politemall_digest.py", timeout=200)
    p = load("politemall_digest.json", {})
    if p.get("note") == "SESSION EXPIRED" or code != 0:
        lines.append("POLITEmall announcements: FAILED or session expired (run login_politemall.py --headless)")
    else:
        unread = {k: v for k, v in (p.get("courses") or {}).items() if v}
        lines.append(f"POLITEmall announcements: {p.get('unread', 0)} new" + (f" ({', '.join(f'{k}:{v}' for k, v in unread.items())})" if unread else ""))
    code, out = run_script("politemall_sync.py", timeout=480)
    last = load("politemall_sync_last.json", {})
    if code == 1:
        lines.append("POLITEmall course sync: SIGN-IN FAILED")
    else:
        new = last.get("new") or []
        lines.append("POLITEmall course files: " + (f"{len(new)} new" if new else "nothing new") + (f", problems: {len(last.get('problems') or [])}" if last.get("problems") else ""))
        lines += [f"    - {n[:110]}" for n in new[:8]]
    code, out = run_script("teams_digest.py", timeout=200)
    if code == 1:
        lines.append("Teams: session expired (run login_politemall.py --headless)")
    elif code != 0:
        lines.append(f"Teams: check FAILED ({out.strip()[-100:] or 'no output'})")
    else:
        lines += [("Teams: " + l.strip().removeprefix("Teams: ")) if i == 0 else l.rstrip() for i, l in enumerate(out.strip().splitlines()) if l.strip()]
    return lines


def quizzes():
    q = (load("quiz_tasks.json", {}) or {}).get("quizzes") or []
    return f"Quizzes (last scan {(load('quiz_tasks.json', {}) or {}).get('scanned_at', '?')[:16]}): " + (f"{len(q)} unfinished" if q else "none unfinished")


def main():
    quick = "--quick" in sys.argv
    with ThreadPoolExecutor(max_workers=4) as ex:  # the browser sources share one profile, so they run in one worker, one after another
        fb = ex.submit(browser_sources, quick)
        fg, ft, fw = ex.submit(gmail), ex.submit(telegram), ex.submit(weather)
        results = [fg.result(), *fb.result(), ft.result(), quizzes(), fw.result()]
    print(f"RETURN CHECK {datetime.now().strftime('%a %d %b %H:%M')}")
    for r in results:
        print(r)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
