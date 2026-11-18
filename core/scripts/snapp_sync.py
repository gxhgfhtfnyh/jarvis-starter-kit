# -*- coding: utf-8 -*-
"""SNApp (RP's student portal, snapp.rp.edu.sg) read-only pull: academic schedule, latest news,
upcoming events. Same Microsoft SSO as PoliteMall, same persistent browser profile -- SNApp forces
an account-picker every single visit (prompt=select_account baked into its own login request), so
this always tries that click; it is a no-op if the picker never appears.

    python snapp_sync.py             # pull, write the digest, print it
    python snapp_sync.py --notify    # same, and DM the owner when there is a new news item or event
    python snapp_sync.py --dry-run   # pull and print only, write nothing

Writes logs/snapp_digest.json: {ts, today, next7, news:[{title,body,age}], events:[{date,time,
location,title}], note}. New-item tracking (so --notify only fires once per item) lives in
logs/snapp_digest_seen.json, keyed by a hash of each item's title (no stable ids on this portal's
text-only home page). Exit codes: 0 ok, 1 sign-in failed, 2 finished with a problem."""
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import inbox_digest_common as C  # noqa: E402
import politemall_config as cfg  # noqa: E402

URL = "https://snapp.rp.edu.sg/#/index/studentHome"
OUT = C.LOGS / "snapp_digest.json"
SEEN = C.LOGS / "snapp_digest_seen.json"
_BOILERPLATE = (
    "In-Person Sync e-learning Async e-learning Assessment",
)


def _clean_lines(text):
    return [ln.strip() for ln in (text or "").splitlines()]


def _slice(lines, start_marker, end_markers):
    """Lines strictly between the first line == start_marker and the next line in end_markers.
    [] if start_marker never appears."""
    try:
        i = lines.index(start_marker)
    except ValueError:
        return []
    out = []
    for ln in lines[i + 1 :]:
        if ln in end_markers:
            break
        out.append(ln)
    return out


_BARE_COUNT = re.compile(r"^\d+$")


def parse_schedule(lines):
    """-> (today_lines, next7_lines), boilerplate/Note lines dropped, blanks dropped. A bare
    number line always belongs to the counter widgets (JOURNEYS/EVENTS/...) below this section,
    never real schedule content, so it also ends the slice."""
    today = [ln for ln in _slice(lines, "Today", ("Next 7 days",)) if ln]
    next7_raw = _slice(lines, "Next 7 days", ("JOURNEYS", "Latest News", "Upcoming Events"))
    next7 = []
    for ln in next7_raw:
        if _BARE_COUNT.match(ln):
            break
        if ln and ln not in _BOILERPLATE and not ln.startswith("Note:"):
            next7.append(ln)
    return today, next7


_COUNT_LINE = re.compile(r"^\d+\s+\d+$")
_AGE_LINE = re.compile(r"^\d+ (day|week|month|hour|minute)s? ago$", re.I)


def parse_news(lines):
    """News block: 'Latest News' .. 'Upcoming Events'. Each item: a view-count line, a title,
    optional body text, 'READ MORE', an age line. Returns [{title, body, age}]."""
    block = _slice(lines, "Latest News", ("Upcoming Events",))
    items, cur = [], None
    for ln in block:
        if _COUNT_LINE.match(ln):
            if cur:
                items.append(cur)
            cur = {"title": "", "body": "", "age": ""}
            continue
        if cur is None:
            continue
        if ln == "READ MORE" or not ln:
            continue
        if _AGE_LINE.match(ln):
            cur["age"] = ln
        elif not cur["title"]:
            cur["title"] = ln
        else:
            cur["body"] = (cur["body"] + " " + ln).strip()
    if cur:
        items.append(cur)
    return items


_DATE_LINE = re.compile(r"^\d{1,2}$")
_MONTH_LINE = re.compile(r"^[A-Z]{3}-\d{2}$")


def parse_events(lines):
    """Events block: 'Upcoming Events' .. 'View All Events'. Each item: a day number, a
    MON-YY line, a title, a 'HH:MM AM/PM - HH:MM AM/PM  Location' line, blank, body, 'View'."""
    block = _slice(lines, "Upcoming Events", ("View All Events",))
    items, cur, stage = [], None, 0
    for ln in block:
        if _DATE_LINE.match(ln) and stage in (0, 4):
            if cur:
                items.append(cur)
            cur = {"date": ln, "month": "", "title": "", "when_where": "", "body": ""}
            stage = 1
            continue
        if cur is None:
            continue
        if stage == 1 and _MONTH_LINE.match(ln):
            cur["month"] = ln
            stage = 2
        elif stage == 2 and ln:
            cur["title"] = ln
            stage = 3
        elif stage == 3 and ln:
            cur["when_where"] = ln
            stage = 4
        elif ln and ln != "View":
            cur["body"] = (cur["body"] + " " + ln).strip()
    if cur:
        items.append(cur)
    return items


def item_key(title):
    return hashlib.sha1((title or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def build_digest(body_text):
    lines = _clean_lines(body_text)
    today, next7 = parse_schedule(lines)
    return {
        "ts": C.now_iso(),
        "today": today or ["No records found"],
        "next7": next7 or ["No records found"],
        "news": parse_news(lines),
        "events": parse_events(lines),
        "note": "OK",
    }


def ensure_signed_in(page):
    """True once the real SNApp home page is showing. SNApp's own login request bakes in
    prompt=select_account, so it bounces through Microsoft's account picker on EVERY visit even
    with a fully valid session -- and which exact picker state renders is flaky/non-deterministic
    (confirmed by hand: sometimes a one-click 'Signed in' account tile, sometimes a generic
    boilerplate 'type your email' realm-discovery screen with no tile at all, for the identical
    already-signed-in account). Handles both, plus a transient stuck boilerplate screen (reload
    and retry) rather than failing on whichever one happens to render this run."""
    for attempt in range(8):
        page.wait_for_timeout(1500)
        body = page.inner_text("body")
        if "Academic" in body:
            return True
        if cfg.USERNAME in body:
            try:
                page.click(f"text={cfg.USERNAME}", timeout=3000)
                page.wait_for_load_state("networkidle", timeout=15000)
                continue
            except Exception:
                pass
        try:
            if page.locator("input[type=email], input[name=loginfmt]").is_visible(timeout=1000):
                page.fill("input[type=email], input[name=loginfmt]", cfg.USERNAME)
                page.click("#idSIButton9")
                continue
        except Exception:
            pass
        if "idBoilerPlateText" in page.content():
            page.reload(wait_until="domcontentloaded")
            continue
    return "Academic" in page.inner_text("body")


def notify(digest, new_news, new_events):
    lines = [f"snapp news: {n['title']}" for n in new_news[:6]]
    lines += [f"snapp event: {e['title']} ({e['date']} {e['month']})" for e in new_events[:6]]
    if not lines:
        return
    msg = "\n".join(lines)
    import quiet_dm
    quiet_dm.send(msg, source="snapp_sync")


def main():
    argv = sys.argv[1:]
    dry = "--dry-run" in argv
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(cfg.PROFILE_DIR, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(URL, wait_until="domcontentloaded", timeout=25000)
            if not ensure_signed_in(page):
                print("SIGN-IN FAILED: could not reach the real SNApp home page", file=sys.stderr)
                import school_health as H
                if not dry:
                    H.report("snapp_sync", False, "snapp sign-in failed")
                return 1
            digest = build_digest(page.inner_text("body"))
        finally:
            ctx.close()

    seen = C.load_json(SEEN, [])
    seen_set = set(seen) if isinstance(seen, list) else set()
    new_news = [n for n in digest["news"] if item_key(n["title"]) not in seen_set]
    new_events = [e for e in digest["events"] if item_key(e["title"]) not in seen_set]

    print(digest)
    if not dry:
        C.atomic_write_json(OUT, digest)
        seen_set |= {item_key(n["title"]) for n in digest["news"]}
        seen_set |= {item_key(e["title"]) for e in digest["events"]}
        C.atomic_write_json(SEEN, sorted(seen_set))
        import school_health as H
        H.report("snapp_sync", True)
        if "--notify" in argv:
            notify(digest, new_news, new_events)
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main())
    except Exception as e:
        print(f"SNAPP SYNC CRASHED: {type(e).__name__}: {e}", file=sys.stderr)
        try:
            import school_health as H
            H.report("snapp_sync", False, f"snapp sync crashed ({type(e).__name__})")
        except Exception:
            pass
        sys.exit(2)
