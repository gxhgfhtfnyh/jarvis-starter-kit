# -*- coding: utf-8 -*-
"""Writes J:\\Jarvis2\\logs\\outlook_digest.json = {"unread": N, "ts": ISO, "note": "..."} for the INBOX WALL OUTLOOK tile.

RP's tenant blocks Graph, so this is browser-only: HEADLESS Playwright with the persistent `politemall` profile (no window
ever opens). Read-only: it navigates, reads, and clicks only the 'Other' / 'Focused' inbox TABS (view switches, not mail
actions). It never opens, flags, moves, deletes or marks a message, and stores counts only (no senders/subjects/bodies).

Count method (Focused and the hidden 'Other' tab are both covered):
  1. Primary: the Inbox folder badge in the folder pane (OWA counts Focused + Other together).
  2. Fallback / cross-check: rendered rows in each tab whose aria-label begins 'Unread' (OWA virtualises long lists, so
     this can undercount a very large backlog). With no badge, unread = focused rows + other rows.
  The note field records which method produced the number.

    python outlook_digest.py
Exit 0 = digest written; exit 1 = failure (previous digest kept) or SESSION EXPIRED (digest written with unread 0)."""
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_digest_common as C

URL = "https://outlook.cloud.microsoft/mail/"
OUT = C.LOGS / "outlook_digest.json"

# Inbox badge: the folder-pane treeitem named Inbox. Returns its label/title/text so Python parses the number.
BADGE_JS = """
() => {
  const items = [...document.querySelectorAll('[role="treeitem"]')];
  const it = items.find((e) => /^\\s*inbox\\b/i.test((e.getAttribute('aria-label') || e.getAttribute('title') || e.innerText || '')));
  if (!it) return null;
  return { label: it.getAttribute('aria-label') || '', title: it.getAttribute('title') || '', text: (it.innerText || '') };
}
"""

# Counts only: how many rendered message rows are unread (OWA prefixes unread rows' aria-label with 'Unread').
ROWS_JS = """
() => {
  const rows = [...document.querySelectorAll('[role="option"]')];
  const n = rows.filter((r) => /^\\s*unread\\b/i.test(r.getAttribute('aria-label') || '')).length;
  return { rows: rows.length, unread: n };
}
"""

# Clicks the inbox view tab with this exact name ('Focused' / 'Other'); returns whether it exists.
TAB_JS = """
(name) => {
  const tab = [...document.querySelectorAll('[role="tab"]')].find((t) => (t.innerText || '').trim() === name);
  if (!tab) return false;
  tab.click();
  return true;
}
"""


def parse_badge(info):
    """Unread number from the Inbox treeitem info dict, or None when it carries no count."""
    if not isinstance(info, dict):
        return None
    for field in ("label", "title"):
        m = re.search(r"(\d[\d,]*)\s+unread", info.get(field) or "", re.I)
        if m:
            return int(m.group(1).replace(",", ""))
    lines = [l.strip() for l in (info.get("text") or "").splitlines() if l.strip()]
    if len(lines) >= 2 and re.fullmatch(r"\d[\d,]*", lines[-1]):
        return int(lines[-1].replace(",", ""))
    return None


def _count(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else 0


def combine(badge, focused, other):
    """(unread, note). focused/other are rendered-unread-row counts; other is None when that tab doesn't exist."""
    detail = f"focused {_count(focused)} other {_count(other)} rows" if other is not None else f"{_count(focused)} rows"
    if badge is not None:
        return badge, f"INBOX BADGE ({detail})"
    return _count(focused) + _count(other), f"ROW COUNT ({detail})"


def settle(page, seconds=3, sleep=time.sleep):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass
    sleep(seconds)


def read_counts(page, sleep=time.sleep):
    """Returns ('expired', None) or ('ok', (badge, focused, other))."""
    page.goto(URL, wait_until="domcontentloaded")
    settle(page, 6, sleep)
    if C.confirm_signed_out(page, URL, sleep):
        return "expired", None
    try:
        page.wait_for_selector('[role="option"], [role="treeitem"]', timeout=20000)
    except Exception:
        pass
    if C.confirm_signed_out(page, URL, sleep):
        return "expired", None
    badge = parse_badge(page.evaluate(BADGE_JS))
    other = None
    if page.evaluate(TAB_JS, "Focused"):  # start from the Focused view
        sleep(1.5)
    r = page.evaluate(ROWS_JS) or {}
    focused = _count(r.get("unread"))
    if page.evaluate(TAB_JS, "Other"):
        sleep(2)
        other = _count((page.evaluate(ROWS_JS) or {}).get("unread"))
        page.evaluate(TAB_JS, "Focused")  # leave the view as it was
    if badge is None and not r.get("rows") and other is None:
        raise RuntimeError("could not read the inbox (no badge, no rows)")
    return "ok", (badge, focused, other)


def run(page, out=OUT, sleep=time.sleep, final=True):
    status, data = read_counts(page, sleep)
    if status == "expired" and not final:
        print("session looks expired; not writing a digest yet")
        return 1
    if status == "expired":
        digest = {"unread": 0, "ts": C.now_iso(), "note": "SESSION EXPIRED"}
        C.atomic_write_json(out, digest)
        print(digest)
        return 1
    unread, note = combine(*data)
    digest = {"unread": int(unread), "ts": C.now_iso(), "note": note}
    C.atomic_write_json(out, digest)
    print(digest)
    return 0


def _once(final):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(C.PROFILE_DIR, headless=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            return run(page, final=final)
        finally:
            context.close()


def main():
    """Under the profile lock; 'SESSION EXPIRED' is only written after a patient check, one re-login and a second check."""
    import school_health as H
    try:
        with H.profile_lock():
            rc = _once(final=False)
            if rc == 1:
                print("session expired on the first check: trying one headless re-login, then checking again")
                H.relogin("outlook_digest")
                rc = _once(final=True)
    except H.LockBusy as e:
        print(f"outlook digest skipped: {e}", file=sys.stderr)
        return 1
    H.report("outlook_digest", rc == 0, "outlook could not be read: session expired and the automatic sign-in did not work")
    return rc


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except Exception as e:  # keep the old digest; exit non-zero so a scheduled run shows as failed
        print(f"outlook digest failed: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            import school_health as H
            H.report("outlook_digest", False, f"outlook check crashed ({type(e).__name__})")
        except Exception:
            pass
        sys.exit(1)
