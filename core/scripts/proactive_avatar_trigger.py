# -*- coding: utf-8 -*-
"""Real decision mechanism for proactive avatar spawning (2026-11-12, the piece flagged open at focus.md:395/411:
avatar_spawn.py gives a clean one-line way to spawn something, and it was demonstrated once, but nothing decides
WHEN to actually use it on its own initiative -- his complaint was "I want YOU to proactively use it instead of
leaving the avatar in the corner and only used when i call it", and a demo once a session doesn't fix that.

Tick5 job. Each tick: look for genuinely newsworthy completed work since the last check (a real intent closed
--result ok, not just a question answered in chat where he already saw the reply), and if he's actually at the PC
free to notice (not away/rdp/locked/gaming/in class) and a cooldown has passed since the last spawn, spawn a card
on his avatar describing what just got done. Deliberately conservative like proactive_status_gate.py: a random
timer would be noise, this only fires on a real completion plus real presence.

Excluded kinds are ones that already got him a direct chat reply (ask_open, unread_link, status_stale,
correction_unlogged, undelivered) -- spawning a card for those too would be redundant, not proactive.

Testable core: is_present(sig), find_new_completions(intents, since_epoch), label_for(intents),
should_spawn(intents, since_epoch, last_spawn_epoch, now, cooldown_min) -> (bool, [intent,...])."""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STATE_PATH = os.path.join(HERE, "..", "logs", "proactive_avatar_state.json")
COOLDOWN_MIN = 20
PRESENT_IDLE_MAX = 300  # seconds; logged in at the console but idle 5+ min isn't "free to notice"
MIN_VALUE = 3

# kinds that already reach him as a direct chat reply when closed -- a card on top would be noise, not news
EXCLUDED_KINDS = {"ask_open", "unread_link", "status_stale", "correction_unlogged", "undelivered"}


def is_present(sig):
    """Pure. True only when he's actually at the PC console and free to notice a spawn right now."""
    sig = sig or {}
    if sig.get("locked") or sig.get("rdp_active") or sig.get("game_on"):
        return False
    if sig.get("own_session_kind") != "console":
        return False
    if sig.get("class_now"):
        return False
    idle = sig.get("idle_s")
    if idle is not None and idle > PRESENT_IDLE_MAX:
        return False
    return True


def find_new_completions(intents, since_epoch):
    """Pure. -> intents closed --result ok after since_epoch, tier<=1, value>=MIN_VALUE, kind not excluded,
    oldest first."""
    out = []
    for i in intents or []:
        if i.get("status") != "done" or i.get("result") != "ok":
            continue
        if i.get("kind") in EXCLUDED_KINDS:
            continue
        if (i.get("tier") or 0) > 1 or (i.get("value") or 0) < MIN_VALUE:
            continue
        closed = i.get("closed_epoch")
        if not closed or closed <= since_epoch:
            continue
        out.append(i)
    out.sort(key=lambda i: i["closed_epoch"])
    return out


def label_for(intents):
    """Pure. -> a short (<=80 char) card label for one or more new completions."""
    if len(intents) == 1:
        text = (intents[0].get("evidence") or intents[0].get("what") or "").strip()
        text = text.split("\n")[0]
        return text[:80] if text else "something just got done"
    return "%d things just got fixed/built" % len(intents)


def should_spawn(intents, since_epoch, last_spawn_epoch, now, cooldown_min=COOLDOWN_MIN):
    """Pure. -> (True, [new completions]) when there's real new work and the cooldown has passed."""
    new = find_new_completions(intents, since_epoch)
    if not new:
        return False, []
    if last_spawn_epoch and (now - last_spawn_epoch) < cooldown_min * 60:
        return False, new  # real work exists but still cooling down -- caller advances since_epoch, not last_spawn
    return True, new


def _read_state():
    try:
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"last_seen_closed_epoch": 0, "last_spawn_epoch": 0}


def _write_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=1)


def _spawn_card(text):
    subprocess.run([sys.executable, os.path.join(HERE, "avatar_spawn.py"), "card", text], timeout=10)


def main(argv=None):
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(r"J:\Jarvis2\logs\proactive_avatar_trigger_off.txt"):
            return
        import intent_store
        import presence_signals

        now = time.time()
        state = _read_state()
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        do_spawn, new = should_spawn(intents, state.get("last_seen_closed_epoch", 0), state.get("last_spawn_epoch", 0), now)
        if not new:
            return
        if do_spawn and is_present(presence_signals.gather()):
            _spawn_card(label_for(new))
            state["last_spawn_epoch"] = now
            state["last_seen_closed_epoch"] = new[-1]["closed_epoch"]
            _write_state(state)
        # else: real work is queued but either still cooling down or he's not actually free to notice right
        # now -- leave last_seen_closed_epoch alone so it's still there to spawn once both gates clear.
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
