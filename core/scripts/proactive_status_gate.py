# -*- coding: utf-8 -*-
"""Proactive status gate (2026-11-12, the "status?" pings named in the log-mining pass tonight: at least 6 explicit
status-check messages from him in two days (10-27/28) despite a rule already existing that I should proactively
status-ping during active work -- the rule existed, nothing enforced it.

Stop hook: if real open/active work exists (an intent genuinely open or doing, not just "waiting on him") and it has
been more than STALE_MINUTES since the last actual Discord send, raise a tier-1 intent nudging a status update.
Deliberately conservative: only fires when there is real open work, never during a quiet stretch with nothing to
report (that would just be noise, the opposite of what's wanted).

Testable core: minutes_since_last_send(sent_rows, now), has_real_open_work(intents), should_nudge(sent_rows, intents, now)."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STALE_MINUTES = 20
SENT_LOG = "discord_sent.jsonl"


def minutes_since_last_send(sent_rows, now):
    """Pure. Minutes since the last row's ISO 'date' field, or None if sent_rows is empty/unparseable."""
    if not sent_rows:
        return None
    import datetime as dt

    last = sent_rows[-1]
    raw = last.get("date")
    if not raw:
        return None
    try:
        ts = dt.datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None
    return (now - ts) / 60.0


def has_real_open_work(intents):
    """Pure. True when any intent is genuinely open or doing (not done/dropped), excluding the status-gate's own kind
    (so its own unclosed nudge doesn't count as "real work" and create a self-sustaining loop), and excluding tier
    2/3 items (ask-first/never) -- those are backlog waiting on him, same bucket as "waiting on the owner", not
    active work I should be proactively reporting progress on. Found 2026-11-12: a stretch of tier-2 unread_link/
    budget_trip/task_disabled backlog kept this nudging every cycle overnight with genuinely nothing new to say."""
    for i in intents or []:
        if i.get("status") in ("open", "doing") and i.get("kind") != "status_stale" and i.get("tier", 0) <= 1:
            return True
    return False


def should_nudge(sent_rows, intents, now, stale_minutes=STALE_MINUTES, presence_mode=None):
    """Pure. True when real open work exists and it's been longer than stale_minutes since the last Discord send.

    2026-11-13: never nudges while presence_mode == "asleep" -- a routine status ping is explicitly NOT a "result,
    a real alert or something a friend would say" (the boot config's own asleep-DM rule), and this gate kept
    re-firing every ~20-30 min overnight with the voice-pipeline operation still genuinely open/blocked, forcing a
    manual close every cycle for a nudge that should never have fired in the first place."""
    if presence_mode == "asleep":
        return False
    if not has_real_open_work(intents):
        return False
    mins = minutes_since_last_send(sent_rows, now)
    if mins is None:
        return False
    return mins > stale_minutes


def _read_jsonl(path):
    rows = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    pass
    except OSError:
        pass
    return rows


def _raise_stale_intent(minutes):
    import hashlib

    import intent_store

    dedupe = "status-stale-%s" % hashlib.sha1(time.strftime("%Y%m%d%H%M").encode()).hexdigest()[:8]
    what = (
        "STATUS STALE: real open work exists and it's been %.0f min since the last Discord message. Send him a quick "
        "status update now, or close this --result skipped if genuinely nothing new to report yet." % minutes
    )
    cand = intent_store.new_intent("status_stale", what, tier=1, value=2, dedupe=dedupe, from_event="proactive_status")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main(argv=None):
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(r"J:\Jarvis2\logs\proactive_status_gate_off.txt"):
            return
        import intent_store
        import set_presence_mode

        logs = intent_store.LOGS
        sent_rows = _read_jsonl(os.path.join(logs, SENT_LOG))
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        now = time.time()
        presence_mode = set_presence_mode.get_mode()
        if not should_nudge(sent_rows, intents, now, presence_mode=presence_mode):
            return
        mins = minutes_since_last_send(sent_rows, now)
        _raise_stale_intent(mins)
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
