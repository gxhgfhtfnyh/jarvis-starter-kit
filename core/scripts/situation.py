# -*- coding: utf-8 -*-
"""One-line live picture of what the owner is doing, built only from signals already on this PC (2026-10-29).
He said: "u only know what i tell you... if i dont tell you you dont know and u dont try to find out". The class schedule, the remote-desktop
session, input idle time, running games and message times were all readable; I just never looked (school miss, 11:07). This puts them in front of
me on every turn (current_time_hook.py prints it). Reads presence_signals.gather(), no network, ~0.02 s. Never raises.
    python situation.py            # prints the line
Testable core: format_situation(sig) is pure."""
import time


def _ago(seconds):
    s = round(seconds)
    if s < 90:
        return "%ds" % s
    if s < 5400:
        return "%dm" % round(s / 60)
    return "%.1fh" % (s / 3600.0)


def format_situation(sig, now_ts=None):
    """'[Situation: class Protein Technologies till 13:00 | on rdp (away from the PC) | input 9s ago | no game | last dm 2m ago]'. Unknown parts are left out."""
    now_ts = sig.get("now_ts", time.time()) if now_ts is None else now_ts
    parts = []
    cls = sig.get("class_now")
    parts.append("class %s till %s" % (cls.get("title", "?"), cls.get("class_end") or cls.get("end", "?")) if cls else "no class now")
    if sig.get("rdp_active"):
        parts.append("on rdp (remote, so not at the PC itself)")
    elif sig.get("own_session_kind") == "console":
        parts.append("at the PC console")
    if sig.get("locked"):
        parts.append("PC locked")
    idle = sig.get("idle_s")
    if idle is not None:
        parts.append("input %s ago" % _ago(idle))
    games = sig.get("games") or []
    parts.append("game: %s" % games[0] if games else "no game")
    if sig.get("game_on"):
        parts.append("game mode ON")
    last = max((sig.get(k) or 0) for k in ("last_discord_ts", "last_telegram_ts", "last_ui_ts"))
    if last:
        parts.append("last msg %s ago" % _ago(max(0, now_ts - last)))
    return "[Situation: " + " | ".join(parts) + "]"


def situation_line():
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import presence_signals
        return format_situation(presence_signals.gather())
    except Exception:
        return "[Situation: unreadable (presence_signals failed); look it up instead of guessing.]"


if __name__ == "__main__":
    print(situation_line())
