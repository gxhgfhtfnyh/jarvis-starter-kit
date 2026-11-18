# -*- coding: utf-8 -*-
"""UserPromptSubmit hook: when a chat message (Discord, Telegram, the UI chat or the terminal) reads like a request for REAL work and no
open operation already covers it, remind the assistant to open its own operation with op_open.py so the work is tracked and recorded on
the operations board (with its own file and stamp), not only in the daily note. Casual chat is explicitly NOT to be opened as an operation.

Silent (prints nothing) unless all of these hold: the prompt is a chat message (a [DISCORD] / [TELEGRAM] / [UI] notification or a typed
terminal prompt; ACTION requests, follow-ups, system events and already-auto-captured messages are ignored), it is longer than 12 words,
it contains a work verb (build, make, fix, add, research, set up, change, create, update, find out ...), and no open card on the board
shares its subject. Throttled to one reminder per 10 minutes. Never raises and always exits 0. The only file it writes is its tiny
throttle state (J:\\Jarvis2\\logs\\op_open_hint_state.json); it never touches the vault, the stores or the chat logs.

Env overrides (tests): JARVIS_VAULT_DIR, ARCHIVE_ACTIONS_FILE, JARVIS_OPEN_HINT_STATE, JARVIS_HINT_NOW (epoch seconds).
"""
import json
import os
import re
import sys
import time
from pathlib import Path

VAULT = Path(os.environ.get("JARVIS_VAULT_DIR") or r"J:\Jarvis-Brain")
ACTIONS = Path(os.environ.get("ARCHIVE_ACTIONS_FILE") or r"J:\Jarvis2\logs\ui_actions.json")
STATE = Path(os.environ.get("JARVIS_OPEN_HINT_STATE") or r"J:\Jarvis2\logs\op_open_hint_state.json")
THROTTLE_S = 10 * 60
MIN_WORDS = 13  # "longer than 12 words"
OPEN = ("sent", "acknowledged", "working")

WORK = re.compile(r"\b(build|make|fix|add|research|set\s?up|change|create|update|find out|implement|design|write|rename|install|remove|"
                  r"refactor|investigate|look into|improve|generate|draft|plan|sort out|organi[sz]e|automate|redo|rework|convert|migrate|"
                  r"develop|configure|integrate|connect|replace|upgrade|clean up|work on|figure out|put together|wire up)\b", re.I)
SKIP_PREFIXES = ("[UI] [ACTION", "[UI-EVENT]", "[OPERATION FOLLOW-UP", "[AUTO-CAPTURED", "[OPERATION PAPER TRAIL")
BODY_SKIP = ("[ACTION", "[UI-EVENT]", "[OPERATION", "[AUTO-CAPTURED")  # what is left after the channel tag and message id are stripped
STOP = set("the a an and or of to in on for with from at by is are be it this that my me i you your we our can could would should please "
           "just also then so up out do does did have has had not no yes as into about all any some more make add fix build set".split())

REMINDER = ("[OPERATION OPENING] This {chan} message reads like a request for real work and no open operation on the board covers it. If it "
            "really is work to do or a decision to track (not casual chat, a joke or a one-line answer), open your own operation FIRST so it is "
            "tracked, with its own file and your stamp: python J:\\Jarvis2\\scripts\\op_open.py --title \"<short title>\" --objective \"<what it is for, "
            "in plain English>\" --done-when \"<what done looks like>\" --source {chan} --ref \"<message id or link>\"  (op_open.py --list-open shows "
            "open ones: add to an existing operation with op_note.py instead of opening a duplicate). Then log numbered steps as you work. Do NOT open "
            "operations for casual conversation.")


def _now():
    try:
        return float(os.environ["JARVIS_HINT_NOW"])
    except (KeyError, ValueError):
        return time.time()


def channel_and_body(prompt):
    """(channel, message text) for a chat prompt, or (None, '') when it is anything else."""
    text = (prompt or "").strip()
    if not text:
        return None, ""
    m = re.search(r"<event>(.*?)</event>", text, re.S)
    if "<task-notification" in text or m:
        event = (m.group(1) if m else text).strip()
    elif text.startswith("[") and text.split("]", 1)[0].upper() in ("[DISCORD", "[TELEGRAM", "[UI"):
        event = text
    elif text.startswith("<") or text.startswith("[SYSTEM") or text.startswith("[Current"):
        return None, ""
    else:
        return "terminal", text  # a typed terminal prompt
    if event.startswith(SKIP_PREFIXES):
        return None, ""
    for tag, chan in (("[DISCORD]", "discord"), ("[TELEGRAM]", "telegram"), ("[UI]", "ui-chat")):
        if event.startswith(tag):
            body = event[len(tag):].strip()
            body = re.sub(r"^\(id \d+\)\s*", "", body)
            if body.startswith(BODY_SKIP):
                return None, ""
            return chan, body
    return None, ""


# Emotional / personal messages are not work, even when they contain a work verb ("i feel like this is what i will be feeling when it
# happens... telling u so that u understand the degrees of sadness", 2026-10-15). A message with feeling words is only treated as work
# when it also contains a clear request marker.
EMOTION = re.compile(r"\b(sad|sadness|cry|crying|cried|tears|feel(?:ing|s)?|felt|lonely|miss(?:ed)?|hurt|hurts|scared|anxious|stress(?:ed)?|"
                     r"grief|lost|love|loved|heartbroken|upset|emotional|thank you|thanks)\b", re.I)
REQUEST = re.compile(r"\b(can you|could you|would you|will you|please|pls|plz|i want you to|i need you to|we need|lets|let'?s|make sure|"
                     r"you should|go ahead and|set up|build me|make me|add a|add an|fix the|fix this)\b", re.I)


def looks_like_work(body):
    words = body.split()
    if EMOTION.search(body) and not REQUEST.search(body):
        return False
    return len(words) >= MIN_WORDS and bool(WORK.search(body)) and not body.lstrip().startswith("[AUTO-CAPTURED")


def _keywords(text):
    return {w for w in re.findall(r"[a-z0-9]{3,}", str(text).lower()) if w not in STOP}


def open_subjects():
    """Titles of the open cards on the board plus the titles of open tracked jobs."""
    titles = []
    try:
        for line in (VAULT / "Active Priorities.md").read_bytes().decode("utf-8", "replace").split("\n"):
            m = re.match(r"^- \[ \] \*\*(.+?)\*\*", line.rstrip("\r"))
            if m:
                titles.append(re.split(r" -- | \u2014 ", m.group(1))[0].strip())
    except OSError:
        pass
    try:
        data = json.loads(ACTIONS.read_text(encoding="utf-8"))
        for a in data.get("actions", []) if isinstance(data, dict) else []:
            if isinstance(a, dict) and a.get("status") in OPEN and a.get("title"):
                titles.append(str(a["title"]))
    except (OSError, ValueError):
        pass
    return titles


def already_covered(body, titles):
    """True when an open card's title shares its subject with the message (a whole title in the text, or 2+ shared meaningful words)."""
    low = " ".join(body.lower().split())
    words = _keywords(body)
    for t in titles:
        tl = " ".join(str(t).lower().split())
        if len(tl) >= 8 and tl in low:
            return True
        tw = _keywords(tl)
        if tw and len(tw & words) >= min(2, len(tw)) and len(tw & words) / len(tw) >= 0.5:
            return True
    return False


def throttled(now):
    try:
        last = float(json.loads(STATE.read_text(encoding="utf-8")).get("ts", 0))
    except (OSError, ValueError, AttributeError, TypeError):
        return False
    return now - last < THROTTLE_S


def remember(now):
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"ts": now}), encoding="utf-8")
    except OSError:
        pass


def main():
    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        return
    prompt = payload.get("prompt") if isinstance(payload, dict) else None
    chan, body = channel_and_body(prompt if isinstance(prompt, str) else "")
    if not chan or not looks_like_work(body):
        return
    if already_covered(body, open_subjects()):
        return
    now = _now()
    if throttled(now):
        return
    remember(now)
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": REMINDER.format(chan=chan)}}))


if __name__ == "__main__":
    try:
        main()
    except Exception:  # a reminder must never break a turn
        pass
    sys.exit(0)
