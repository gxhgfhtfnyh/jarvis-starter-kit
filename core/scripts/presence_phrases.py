# -*- coding: utf-8 -*-
"""Presence from his own words (2026-10-30; he said the presence still was not set right consistently).

Why: the scoring model in presence_auto.py can only guess from the PC and the clock, and without a registered phone it cannot see where he is. What he SAYS is the best
evidence there is ("im at sch now lol", "im home", "gn", "gm"), and until now only I acted on it, when I happened to be looking at the message: this morning I noticed "im at sch"
five minutes late and the mode stayed home for an hour. This reads his new DMs every 5 minutes (a row in the job table) and sets the mode silently (no DM: he does not want
reports), with PRESENCE_CHANGED_BY=phrase so presence_auto holds off for 4 hours instead of overruling his word with a weak signal. Each home or away statement is also a
free label for finding his phone: it triggers a network snapshot (presence_find_phone.py) so the phone's address is learned from the difference between the labelled scans.
    python presence_phrases.py             # act on new messages
    python presence_phrases.py --dry-run   # print what it would do, change nothing
Pure core (unit tested): detect(text) -> "home" | "away" | "asleep" | None, newest_hit(rows, last_id). Kill switch: logs/presence_phrases_off.txt."""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
THE_OWNER_ID = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it
NO_WINDOW = 0x08000000
PLACE = r"(?:the\s+)?(?:sch|school|class|lecture|lesson|tuition|uni|work|gym|mall|town|library|lab|campus|rp)"
ASLEEP = [r"\b(?:gn|good ?night|night night|nini)\b", r"\b(?:going|gonna|about) to (?:sleep|bed)\b", r"\b(?:off|heading) to (?:sleep|bed)\b", r"\bsleep(?:ing)? now\b", r"\bgot to sleep\b"]
AWAY = [r"\b(?:i'?m|im|i am)\s+(?:now\s+)?(?:at|in|going to|heading to|off to|gonna be at|on my way to|reaching)\s+" + PLACE + r"\b",
        r"\b(?:i'?m\s*|im\s*|i am\s+)(?:out|leaving|heading out|going out)\b(?!\s+of\b)", r"\b(?:off|heading) to " + PLACE + r"\b", r"^at (?:sch|school|class)\b",
        r"\b(?:bouta|about to|gonna|going to)\s+(?:head|go|leave)\s+(?:to\s+|for\s+)?" + PLACE + r"\b"]
HOME = [r"\b(?:i'?m\s*|im\s*|i am\s+)(?:now\s+)?(?:home|back home|back)\b", r"\b(?:just\s+)?(?:got|reached|came|arrived)\s+home\b", r"\bhome now\b",
        r"\b(?:back|home) from " + PLACE + r"\b", r"^(?:gm|good morning|morning)\b", r"\bjust woke up\b", r"\b(?:i'?m|im) up\b"]
FUTURE = re.compile(r"\b(?:when|whenever|until|till|once|after|if|before|ltr|later|wen)\b[^.!\n]{0,20}$")      # "when im home", "keep working till im back": a plan, not a fact
NEGATION = re.compile(r"\b(?:not|isnt|isn't|aint|ain't|never|wasnt|wasn't|dont|don't|no longer)\b[^.!\n]{0,25}$")


def _hit(patterns, text):
    for p in patterns:
        for m in re.finditer(p, text):
            before = text[:m.start()]
            if not NEGATION.search(before) and not FUTURE.search(before):
                return True
    return False


def detect(text):
    """Pure. What his message says about where he is, or None. First person only, negations/future-tense/pure questions are ignored; asleep beats away beats home.
    Real bug found 2026-11-06: this used to reject the WHOLE message if a "?" appeared anywhere ("im home, did u see this?" was silently ignored), not just
    messages that are themselves a question with no statement. The ASLEEP/AWAY/HOME patterns are already first-person-anchored (require "i'm"/"im"/"i am"), so a
    genuine question addressed to me ("are you home?") was never going to false-positive on them anyway - the blanket "?" guard only cost real detections."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if _hit(ASLEEP, t):
        return "asleep"
    if _hit(AWAY, t):
        return "away"
    if _hit(HOME, t):
        return "home"
    return None


def newest_hit(rows, last_id):
    """Pure. -> (mode, message id) of the newest message from him after `last_id` that says something about where he is, else (None, None)."""
    best = (None, None)
    for r in rows or []:
        try:
            mid = int(r.get("id"))
        except (TypeError, ValueError):
            continue
        if mid <= (last_id or 0) or r.get("author_id") != THE_OWNER_ID or r.get("source") != "dm":
            continue
        mode = detect(r.get("text"))
        if mode:
            best = (mode, mid)
    return best


def _rows():
    out = []
    try:
        with open(os.path.join(LOGS, "discord_incoming.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _state_path():
    return os.path.join(LOGS, "presence_phrases_state.json")


def _apply(mode):
    env = dict(os.environ, PRESENCE_CHANGED_BY="phrase")
    subprocess.run([sys.executable, os.path.join(HERE, "set_presence_mode.py"), mode], env=env, capture_output=True, timeout=90, creationflags=NO_WINDOW if os.name == "nt" else 0)


def _snapshot(mode):
    """A labelled network scan for the phone search (home and away only), started and left to finish on its own."""
    if mode in ("home", "away"):
        subprocess.Popen([sys.executable, os.path.join(HERE, "presence_find_phone.py"), "snapshot", mode], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=(NO_WINDOW | 0x00000008) if os.name == "nt" else 0)


def run(argv=None, rows=None, apply=None, snapshot=None, current=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if os.path.exists(os.path.join(LOGS, "presence_phrases_off.txt")):
        print("presence_phrases: off")
        return 0
    rows = _rows() if rows is None else rows
    try:
        last = int(json.load(open(_state_path(), encoding="utf-8")).get("last_id", 0))
    except (OSError, ValueError):
        last = 0
    newest = max((int(r["id"]) for r in rows if str(r.get("id", "")).isdigit()), default=last)
    if last == 0:                                              # first run: start from now, do not act on old messages
        if "--dry-run" not in argv:
            json.dump({"last_id": newest}, open(_state_path(), "w", encoding="utf-8"))
        print("presence_phrases: first run, cursor set to %d" % newest)
        return 0
    mode, mid = newest_hit(rows, last)
    if "--dry-run" in argv:
        print("would set %s from message %s" % (mode, mid) if mode else "nothing to do")
        return 0
    if mode:
        cur = current if current is not None else _current_mode()
        _same = cur == mode
        if not _same or _last_change_by() != "phrase":         # a statement of his is recorded as his word even when the mode already matches (it extends the hold)
            (apply or _apply)(mode)
        (snapshot or _snapshot)(mode)
    json.dump({"last_id": newest}, open(_state_path(), "w", encoding="utf-8"))
    print("presence_phrases: %s" % (("set %s from message %s" % (mode, mid)) if mode else "no location words in new messages"))
    return 0


def _current_mode():
    try:
        from set_presence_mode import get_mode
        return get_mode()
    except Exception:                                          # noqa: BLE001
        return None


def _last_change_by():
    try:
        return json.load(open(os.path.join(LOGS, "presence_last_change.json"), encoding="utf-8")).get("by")
    except (OSError, ValueError):
        return None


if __name__ == "__main__":
    sys.exit(run())
