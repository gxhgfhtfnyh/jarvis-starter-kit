# -*- coding: utf-8 -*-
"""Watcher-Monitor Stop gate (2026-11-13, "I have remined u countless times that the discord
watcher expired" -- a real miss: the Monitor expired at 12:57 AM and nothing forced a re-arm until
he said something, again, despite the delivery-guarantee fix built earlier the same night).

inbox_hook.py and unread_dm_gate.py already guarantee no DM is silently missed regardless of
Monitor state. This gate is for the OTHER half of the complaint: the live Monitor itself lapsing
and nobody noticing/re-arming it, which erodes trust even when delivery never actually broke. Paired
with watcher_monitor_track.py (a PostToolUse observer that stamps logs/watcher_monitor_last_armed.txt
every time the watcher Monitor is armed): if it has been armed before but lapsed for too long, raise
an intent so keep_going.py forces a re-arm, instead of relying on noticing the expiry notice.

Never fires if the Monitor has never been armed this session (nothing to lapse from). Kill switch:
logs/watcher_monitor_gate_off.txt.
Testable core: lapsed(last_armed, now, threshold_s)."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

STAMP = r"J:\Jarvis2\logs\watcher_monitor_last_armed.txt"
OFF = r"J:\Jarvis2\logs\watcher_monitor_gate_off.txt"
THRESHOLD_S = 420  # 7 min: the Monitor's REAL observed cap tonight (2026-11-15) is 5 min, not
# the 30 min this used to assume -- every single re-arm all evening expired at exactly 5 min.
# 7 min is 5 + a small grace window, not 35, so a missed re-arm is caught fast instead of
# silently going unreacted-to for most of a half hour (his direct ask: "rearm 100% of the time").


def lapsed(last_armed, now, threshold_s=THRESHOLD_S):
    """Pure. True when the watcher Monitor was armed before but it has been over threshold_s since."""
    if last_armed is None:
        return False
    return (now - last_armed) > threshold_s


def _last_armed():
    try:
        return float(open(STAMP, encoding="utf-8").read().strip())
    except Exception:
        return None


def _raise_lapsed_intent(last_armed, now):
    import intent_store

    gap_min = round((now - last_armed) / 60)
    what = (
        "WATCHER MONITOR LAPSED: the live Discord/UI watcher Monitor has been unarmed for about %d minutes "
        "(it only covers live same-second visibility; inbox_hook.py/unread_dm_gate.py already guarantee no DM "
        "is missed either way). Re-arm it now: Monitor(command='tail -f -n 0 J:/Jarvis2/logs/combined_watcher_live.log', "
        "description='live Discord/UI watcher feed', timeout_ms=1800000)." % gap_min
    )
    cand = intent_store.new_intent("watcher_monitor_lapsed", what, tier=1, value=2, dedupe="watcher-lapsed", from_event="watcher_monitor_gate")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(OFF):
            return
        now = time.time()
        last_armed = _last_armed()
        if lapsed(last_armed, now):
            _raise_lapsed_intent(last_armed, now)
    except Exception:
        return


if __name__ == "__main__":
    main()
