# -*- coding: utf-8 -*-
"""Cheap school session check (task JarvisSchoolSessionCheck, every 45 min from 06:45 to ~23:00; the 06:45 run is the proactive morning window).
Headless, under the profile lock. Opens the POLITEmall home page (read-only, nothing is submitted):
  * signed in  -> logs "ok" (also refreshes the Microsoft cookies) and clears the login alert state;
  * signed out -> ONE automatic re-login via school_health.relogin() (login_politemall.py state machine; at most one attempt per 2 h
    after a failed one), then checks again. Two failed re-logins in a row -> Discord DM "school check failed: ...", at most one per 6 h.
Log: logs/school_session_check.log (one line per run, with the persistent Microsoft cookie expiry). No credentials are read here.

    python school_session_check.py             # check, re-login only if signed out
    python school_session_check.py --no-login  # check only (never signs in)
"""
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import inbox_digest_common as C  # noqa: E402
import school_health as H  # noqa: E402

LOGFILE = C.LOGS / "school_session_check.log"
HOME = "https://rplms.polite.edu.sg/d2l/home"


def log(msg):
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line)
    try:
        with open(LOGFILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def probe():
    """(signed_in, persistent_cookie_expiry_text). Opens, looks, closes."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(C.PROFILE_DIR, headless=True)
        try:
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(HOME, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            page.wait_for_timeout(3000)
            ok = not C.confirm_signed_out(page, HOME)
            exp = "none"
            for c in ctx.cookies():
                if c["name"] == "ESTSAUTHPERSISTENT":
                    exp = datetime.fromtimestamp(c["expires"]).strftime("%Y-%m-%d %H:%M") if c["expires"] > 0 else "session-only"
            return ok, exp
        finally:
            ctx.close()


def main():
    no_login = "--no-login" in sys.argv
    ok, exp = probe()
    if ok:
        log(f"ok: signed in (Microsoft persistent cookie expires {exp})")
        H.report("school_session", True)
        H.report("school_login", True)
        return 0
    log(f"signed OUT (Microsoft persistent cookie: {exp})")
    if no_login:
        return 1
    logged = H.relogin("school_session_check")
    st = H._load()
    log(f"re-login {'worked' if logged else 'did not work or was skipped'} (consecutive failures {st.get('relogin_fails', 0)}; "
        f"last: {st.get('last_relogin_reason') or 'none'})")
    ok, exp = probe()
    log(f"after re-login: {'signed in' if ok else 'STILL signed out'} (cookie {exp})")
    if ok:
        H.report("school_session", True)
        H.report("school_login", True)
        return 0
    if int(H._load().get("relogin_fails", 0)) >= 2:
        H.report("school_session", False, f"school is signed out and the automatic sign-in failed twice ({st.get('last_relogin_reason') or 'unknown'})")
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if sys.stdout is None:
        sys.stdout = sys.stderr = open(C.LOGS / "school_session_check.out", "a", encoding="utf-8")
    sys.exit(H.locked_main(main, "session check"))
