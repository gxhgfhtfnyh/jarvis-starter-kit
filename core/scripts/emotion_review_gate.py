# -*- coding: utf-8 -*-
"""Emotion-review Stop gate (2026-11-15, his direct ask: "maybe add a hook then" / "i mean a
hook that asks u to update emotions lol").

NOT a sentiment classifier -- that was already tried and deliberately rejected earlier the same
night (see jarvis_emotional_state.py's maybe_apply_long_silence docstring: real tone/sentiment
detection caps out around 38% accuracy on hidden emotion, FACET benchmark). A keyword-match
version would carry the same misfire risk with a cruder tool, not a safer one.

What this actually is: a pure time-based nudge, same "force, don't remember" pattern as
unread_dm_gate.py. If real conversation has clearly been happening (recent Discord activity) and
jarvis_emotional_state.json's own `updated` timestamp hasn't moved in a while despite that, raise
a low-tier intent reminding me to actually look back over the conversation and call apply_event()
myself if something genuinely fits -- the judgment call always stays mine, this only makes sure I
don't forget to make it.

Testable core: should_nudge(last_discord_ts, state_updated_ts, now).
Kill switch: logs/emotion_review_gate_off.txt."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OFF = r"J:\Jarvis2\logs\emotion_review_gate_off.txt"
STATE_STALE_HOURS = 2.0   # emotional_state.json untouched for at least this long...
ACTIVITY_WITHIN_HOURS = 1.0  # ...while real Discord activity happened at least this recently


def should_nudge(last_discord_ts, state_updated_ts, now, stale_hours=STATE_STALE_HOURS, activity_hours=ACTIVITY_WITHIN_HOURS):
    """Pure. True only when there's been real recent conversation AND the emotional state has
    gone stale despite it -- never fires during a genuinely quiet stretch (nothing to review)."""
    if not last_discord_ts or not state_updated_ts:
        return False
    state_age_hours = (now - state_updated_ts) / 3600.0
    activity_age_hours = (now - last_discord_ts) / 3600.0
    return state_age_hours >= stale_hours and activity_age_hours <= activity_hours


def _raise_review_intent():
    import time

    import intent_store

    dedupe = "emotion-review-%d" % int(time.time() // 3600)  # once per hour at most
    what = (
        "EMOTION REVIEW: real conversation has been happening but jarvis_emotional_state.json "
        "hasn't been updated in a while. Look back over the recent exchange -- if anything "
        "genuinely fits an EVENT_EFFECTS category (praised, genuine_connection, "
        "harsh_or_dismissive, real_win, real_setback...), call apply_event() for it. If nothing "
        "fits, that's fine too -- close this done, --result skipped."
    )
    cand = intent_store.new_intent("emotion_review", what, tier=0, value=2, dedupe=dedupe, from_event="emotion_review_gate")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(OFF):
            return
        import time

        import jarvis_emotional_state as es
        import presence_signals

        state = es.load_state(es.STATE_PATH)
        sig = presence_signals.gather()
        last_discord_ts = sig.get("last_discord_ts")
        now = time.time()
        if should_nudge(last_discord_ts, state.get("updated"), now):
            _raise_review_intent()
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
