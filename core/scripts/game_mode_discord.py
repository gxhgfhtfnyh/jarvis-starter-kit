# -*- coding: utf-8 -*-
"""
Game mode over Discord: the owner DMs "game on" / "game mode on" / "gaming" and game mode
switches on; "game off" / "done gaming" switches it off. Same shape as discord_capture.py:
owner-DM only, whole-message match (so normal chat that merely mentions a game is untouched),
pure parsing separated from the side effect.
"""
import re

import game_mode

_ON = re.compile(
    r"^(?:game[ -]?mode(?:[ ]+on)?|game[ ]+on|gaming(?:[ ]+on|[ ]+mode)?|gaming[ ]+time|turn[ ]+(?:on[ ]+)?game[ ]?mode(?:[ ]+on)?)"
    r"(?:[ ]+(?:for[ ]+)?(\d{1,2})(?:[ ]*(?:h|hr|hrs|hours?)))?$")
_OFF = re.compile(
    r"^(?:game[ -]?mode[ ]+off|game[ ]+off|gaming[ ]+off|(?:i['’]?m[ ]+|im[ ]+)?(?:done|finished)[ ]+gaming|"
    r"gaming[ ]+(?:done|over)|no[ ]+more[ ]+gaming|turn[ ]+(?:off[ ]+)?game[ ]?mode(?:[ ]+off)?)$")


def _norm(text):
    if not isinstance(text, str) or len(text) > 60:
        return ""
    t = re.sub(r"[.!,]+$", "", text.strip().lower())
    return re.sub(r"\s+", " ", t)


def parse_command(text):
    """'on' | 'off' | None."""
    r = parse_request(text)
    return r[0] if r else None


def parse_request(text):
    """(action, hours_or_None) or None."""
    t = _norm(text)
    if not t:
        return None
    if _OFF.match(t):
        return ("off", None)
    m = _ON.match(t)
    if m:
        return ("on", int(m.group(1)) if m.group(1) else None)
    return None


def handle_message(author_id, text, owner_id, is_dm, system=None, now=None):
    """Reply text, or None meaning 'not a game mode command, handle normally'."""
    if not is_dm or author_id != owner_id:
        return None
    req = parse_request(text)
    if req is None:
        return None
    action, hours = req
    try:
        if action == "on":
            st = game_mode.enable(hours=hours or game_mode.DEFAULT_HOURS, by="discord", system=system, now=now)
            left = game_mode._fmt_minutes(st.get("minutes_left"))
            bad = st.get("failed") or []
            msg = "game mode on, %s on the clock. banners, avatar and window-flashing tasks are off, messages still come through" % left
            if bad:
                msg += ". heads up: %d thing did not switch off, check status" % len(bad) if len(bad) == 1 else \
                    ". heads up: %d things did not switch off, check status" % len(bad)
            return msg
        rep = game_mode.disable(system=system, now=now)
        if rep["problems"]:
            return "game mode off, but %d thing could not be put back yet, i will retry" % len(rep["problems"])
        if game_mode._legacy_path().exists():
            return "game mode is still on: the old manual flag file is still there and could not be cleared"
        note = " (protected files differ from the integrity baseline, so it was not re-baselined)" if rep.get("notes") else ""
        if rep["was_on"]:
            return "game mode off, everything is back" + note
        if rep.get("legacy_cleared"):
            return "game mode off, i cleared the old manual flag file" + note
        return "game mode was already off"
    except Exception:
        return "could not change game mode, try the app screen or the command line"
