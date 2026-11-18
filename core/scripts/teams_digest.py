# -*- coding: utf-8 -*-
"""Microsoft Teams digest for the return check: the unread count (from the page title) and, only when something is unread, the newest
Activity items (who mentioned / added / replied, in which team, when). Headless, uses the signed-in school profile
(login_politemall.py signs it in). It never replies to, reacts to or sends anything.

SIDE EFFECT (deliberate, banked 2026-10-20): opening Teams' Activity list marks its items as read, which clears the Teams badge.
So it opens Activity ONLY when the unread count is above 0, and prints what it saw so nothing is lost. With 0 unread it touches nothing.
Chats are not read (Teams shows no reliable unread marker there); the title count already includes them, so a count higher than the
items listed means some are chats. The feed is newest first, so the first N items are taken as the N unread ones (approximate).

    python teams_digest.py          # print the digest and write logs/teams_digest.json
Exit codes: 0 ok, 1 sign-in expired, 2 could not read the page."""
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_digest_common as C  # noqa: E402

URL = "https://teams.cloud.microsoft/"
OUT = C.LOGS / "teams_digest.json"
JS_ITEMS = """() => [...document.querySelectorAll('[data-tid="activity-feed-list-item"]')].slice(0, 12).map(e => ({
  title: ((e.querySelector('[data-tid="activity-feed-item-title"]') || {}).innerText || '').trim(),
  text: (e.innerText || '').split('\\n').map(s => s.trim()).filter(Boolean)}))"""


def main():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(C.PROFILE_DIR, headless=True, viewport={"width": 1500, "height": 950})
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded")
            page.wait_for_timeout(25000)
            if C.signed_out(page.url):
                print("SESSION EXPIRED: run login_politemall.py --headless", file=sys.stderr)
                return 1
            m = re.match(r"\((\d+)\)", page.title())
            unread = int(m.group(1)) if m else 0
            items = []
            if unread > 0:
                try:
                    page.locator('button[aria-label^="Activity"]').first.click()
                    page.wait_for_timeout(5000)
                    for it in page.evaluate(JS_ITEMS)[:max(unread, 1)]:
                        lines = it["text"]
                        when = next((l for l in lines if re.fullmatch(r"\d{1,2}/\d{1,2}|\d{1,2}:\d{2}.*", l)), "")
                        preview = next((l for l in lines[1:] if l != when and l != it["title"]), "")
                        if it["title"]:
                            items.append({"title": it["title"][:120], "when": when, "preview": preview[:90]})
                except Exception as e:
                    print(f"could not read the Activity list ({type(e).__name__}); the count is still valid", file=sys.stderr)
            if not page.title():
                return 2
        finally:
            ctx.close()
    C.atomic_write_json(OUT, {"ts": datetime.now().isoformat(timespec="seconds"), "unread": unread, "activity": items})
    print(f"Teams: {unread} unread" + (f", newest {len(items)} activity item(s):" if items else ""))
    for it in items[:8]:
        print(f"  - {it['title']}" + (f" ({it['when']})" if it["when"] else "") + (f": {it['preview']}" if it["preview"] else ""))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import school_health as H
    try:
        with H.profile_lock():
            sys.exit(main())
    except H.LockBusy as e:
        print(f"teams digest skipped: {e}", file=sys.stderr)
        sys.exit(2)
