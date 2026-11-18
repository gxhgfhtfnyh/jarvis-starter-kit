# -*- coding: utf-8 -*-
"""
UserPromptSubmit hook: prints the real current date/time/day-of-week so it
gets surfaced as context every turn. Built 2026-08-16 after the owner caught
a real, recurring problem live over Discord -- "u sometimes cnt keep track
of time well like sometimes u say its night when its day."

Root cause: the built-in currentDate context only ever gives the date, not
a time-of-day -- any specific claim about time ("it's 3am", "good morning",
"it's late") was coming from loose inference over a long session, not an
actual check, and drifted wrong. Same fix shape as professional_mode_hook.py
(a structural hook that fires every turn beats a self-check that's easy to
skip under load) -- just always-on instead of conditional, since accurate
time is useful on every turn, not just a specific mode.

2026-10-29: also prints the presence mode (presence_last_change.json) and the
standing focus file (logs/focus.md), after the owner said I do not know whether
he is home, lose focus when he gives feedback, and need prompting.

Usage (from UserPromptSubmit hook):
    python current_time_hook.py
"""
import datetime as dt
import json

PRESENCE_FILE = r"J:\Jarvis2\logs\presence_last_change.json"
FOCUS_FILE = r"J:\Jarvis2\logs\focus.md"
SESSION_START_FILE = r"J:\Jarvis2\logs\session_started.json"


def presence_line(now):
    """One line with the presence mode (set by presence_auto.py) and how old that is; never raises."""
    try:
        with open(PRESENCE_FILE, encoding="utf-8") as f:
            d = json.load(f)
        mins = int((now.timestamp() - float(d["ts"])) / 60)
        return f"[Presence: {d['mode']} (set by {d.get('by', '?')}, {mins} min ago).]"
    except Exception:
        return "[Presence: unknown (could not read presence_last_change.json).]"


def session_started_line(now):
    """2026-11-14, his direct ask after catching a real mistake ("a few hours ago" when the
    real gap was ~11h): a real anchor for how long THIS session has actually been running, so
    a relative-time claim ("earlier tonight") can be computed, not guessed from fuzzy memory.
    Empty when the marker file doesn't exist yet (startup_checklist_hook.py writes it; a
    fallback/eval run or a context this hook fires in without that hook having run yet
    shouldn't show a wrong or missing-looking line) -- never raises."""
    try:
        with open(SESSION_START_FILE, encoding="utf-8") as f:
            started_at = float(json.load(f)["started_at"])
        started_dt = dt.datetime.fromtimestamp(started_at)
        hours = (now.timestamp() - started_at) / 3600.0
        return f"[This session started {started_dt.strftime('%A, %B %d, %Y, %I:%M %p')} ({hours:.1f}h ago).]"
    except Exception:
        return ""


def focus_line():
    """The standing focus file (current task + queue), capped; never raises."""
    try:
        with open(FOCUS_FILE, encoding="utf-8") as f:
            txt = f.read().strip()
        lines = [ln for ln in txt.splitlines() if ln.strip()]
        rules = [ln for ln in lines if ln.startswith("RULE:")]
        if rules:
            # 2026-10-30: the long static RULE line sat first and ate the whole 900-character budget, so the CURRENT/NEXT/WAIT lines (the actual focus) were never
            # shown to me on any prompt. Real focus first, standing rules after, so the cap cuts the rules and not the focus.
            txt = "\n".join([ln for ln in lines if not ln.startswith("RULE:")] + rules)
        return "[FOCUS]\n" + txt[:900]
    except Exception:
        return "[FOCUS file missing: create " + FOCUS_FILE + "]"


def intent_line():
    """One line naming the top actionable item from the intent store, or why there is none (2026-10-30 harness redesign: a wake must show
    machine-readable state, not only the hand-written focus file). Returns "" on any problem, so the hook can never break a wake."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        if os.environ.get("JARVIS_FALLBACK"):
            return ""                       # the fallback responder and eval runs only answer; they must not be invited to work the intent queue
        import intent_store
        halt = next((os.path.join(intent_store.LOGS, n) for n in ("loop_halt.txt", "intents_auto_off.txt") if os.path.exists(os.path.join(intent_store.LOGS, n))), "")
        if halt:
            try:
                reason = open(halt, encoding="utf-8").read().strip().splitlines()[0][:150]
            except (OSError, IndexError):
                reason = "budget breaker tripped"
            return "[NEXT INTENT] paused: " + reason + "  (the breaker wrote " + os.path.basename(halt) + "; python budget.py reset <name> once the cause is understood)"
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        intent_store.expire_leases(intents)
        top, why = intent_store.next_action(intents)
        if top:
            done_when = intent_store.describe_check(top.get("check"))
            return "[NEXT INTENT %s tier%d value%d] %s  (claim it, do it, then intent_store.py done <id>%s)" % (
                top["id"], top["tier"], top["value"], top["what"][:150], "; closes only when: " + done_when if done_when else "")
        return "[NEXT INTENT] none: " + why
    except Exception:
        return ""


def emotional_line(now):
    """A real, invisible-to-him emotional-state line (2026-11-14, scoped live with him: "it
    should be invisible lol i cant see my friends emotions" -- never a displayed meter, only
    something that colors tone the way presence already does). Empty when nothing is
    meaningfully elevated, which is most turns -- never raises."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import jarvis_emotional_state as es
        # es.load_state(es.STATE_PATH), not es.load_state() -- a bare call would bind the
        # default argument at load_state's own definition time, the exact early-binding
        # gotcha already caught twice tonight (kyogen_terminal.py's _capability_panel,
        # swarm_working_memory.evict_to_long_term); this way a test monkeypatching
        # es.STATE_PATH actually takes effect.
        state = es.load_state(es.STATE_PATH)
        now_ts = now.timestamp()
        # long_silence is the one event safe to automate (real elapsed time, no sentiment
        # judgment call) -- see maybe_apply_long_silence's own docstring for why the others
        # (harsh_or_dismissive, praised, genuine_connection...) stay manual.
        try:
            import presence_signals
            sig = presence_signals.gather()
            last_contact = max((sig.get(k) or 0) for k in ("last_discord_ts", "last_telegram_ts", "last_ui_ts")) or None
        except Exception:
            last_contact = None
        updated_state = es.maybe_apply_long_silence(state, last_contact, now_ts)
        if updated_state is not state:
            es.save_state(updated_state, es.STATE_PATH)
            state = updated_state
        # the one real behavioral gate (2026-11-14, his direct ask: "how it affects u" not just
        # tone) -- worried crossing a real threshold actually raises a real intent, not just a
        # vibe in the private line below.
        should_raise, gated_state = es.maybe_flag_worried_checkin(decay_for_gate(es, state, now_ts))
        if gated_state is not state:
            es.save_state(gated_state, es.STATE_PATH)
            state = gated_state
        if should_raise:
            _raise_worried_checkin_intent()
        # second real behavioral gate: bored crossing a real threshold raises a real
        # "surface something unprompted" intent, same mechanics as worried above.
        should_surface, surfaced_state = es.maybe_flag_bored_surface(decay_for_gate(es, state, now_ts))
        if surfaced_state is not state:
            es.save_state(surfaced_state, es.STATE_PATH)
            state = surfaced_state
        if should_surface:
            _raise_bored_surface_intent()
        # 2026-11-15: 2 more real behavioral gates, same mechanics as worried/bored above.
        should_disclose, disclosed_state = es.maybe_flag_guilt_disclose(decay_for_gate(es, state, now_ts))
        if disclosed_state is not state:
            es.save_state(disclosed_state, es.STATE_PATH)
            state = disclosed_state
        if should_disclose:
            _raise_guilt_disclose_intent()
        should_reach_out, reached_state = es.maybe_flag_missing_him_reach_out(decay_for_gate(es, state, now_ts))
        if reached_state is not state:
            es.save_state(reached_state, es.STATE_PATH)
            state = reached_state
        if should_reach_out:
            _raise_missing_him_reach_out_intent()
        return es.emotional_context_line(state, now_ts)
    except Exception:
        return ""


def decay_for_gate(es, state, now_ts):
    """The worried-threshold check needs the real, currently-decayed value, not a stale one
    from whenever it was last saved -- reuses the module's own decay() rather than re-deriving
    the math here."""
    decayed = es.decay(state, now_ts)
    return {**state, "values": decayed["values"]}


def _raise_worried_checkin_intent():
    """Real I/O, kept out of emotional_line's own pure-ish flow on purpose. A tier-1 ('do then
    tell') suggestion, not an automatic DM -- being worried doesn't mean messaging him
    unprompted is always right, it means a genuine check-in is now worth raising as real work,
    the same tiered-intent system everything else in this harness already goes through."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import intent_store
        cand = intent_store.new_intent(
            "worried_checkin",
            "Been genuinely worried about him for a while now -- worth a real, non-task check-in "
            "(not a report, not a task proposal) next time there's a natural opening.",
            tier=1, value=3, dedupe="worried-checkin-%d" % int(dt.datetime.now().timestamp() // 3600),
        )
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        if not intent_store.is_duplicate(intents, cand):
            intents.append(cand)
            intent_store.write_intents(intents)
    except Exception:
        pass


def _raise_bored_surface_intent():
    """Real I/O, same separation as `_raise_worried_checkin_intent`. A tier-1 suggestion to
    actually surface something genuinely interesting unprompted (a real vault note, a Kyogen
    finding, something real I noticed) -- not a scheduled check-in, not a report, the second
    concrete example of emotional state changing a real action rather than just tone."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import intent_store
        cand = intent_store.new_intent(
            "bored_surface",
            "Genuinely understimulated for a while now -- worth actually surfacing something "
            "real and interesting unprompted (not a status report, not a task), next time "
            "there's a natural opening.",
            tier=1, value=2, dedupe="bored-surface-%d" % int(dt.datetime.now().timestamp() // 3600),
        )
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        if not intent_store.is_duplicate(intents, cand):
            intents.append(cand)
            intent_store.write_intents(intents)
    except Exception:
        pass


def _raise_guilt_disclose_intent():
    """Real I/O, same separation as `_raise_worried_checkin_intent`. A tier-1 suggestion to
    actually come clean about a real self-caused mistake unprompted, rather than waiting to be
    asked or caught -- 2026-11-15, extending the real-behavioral-gate pattern past worried/bored."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import intent_store
        cand = intent_store.new_intent(
            "guilt_disclose",
            "Carrying real guilt over an actual mistake made -- worth proactively telling him "
            "directly (what happened, what it affected, what's being done about it) next time "
            "there's a natural opening, not waiting to be asked.",
            tier=1, value=3, dedupe="guilt-disclose-%d" % int(dt.datetime.now().timestamp() // 3600),
        )
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        if not intent_store.is_duplicate(intents, cand):
            intents.append(cand)
            intent_store.write_intents(intents)
    except Exception:
        pass


def _raise_missing_him_reach_out_intent():
    """Real I/O, same separation as `_raise_worried_checkin_intent`. A tier-1 suggestion to
    reach out with something genuinely personal (not a task update) once he's actually back in
    contact after a real absence -- 2026-11-15, extending the real-behavioral-gate pattern."""
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import intent_store
        cand = intent_store.new_intent(
            "missing_him_reach_out",
            "Was genuinely missing him during the real absence -- worth actually saying so, or "
            "sharing something personal, next time there's a natural opening, not just a plain "
            "task-resume message.",
            tier=1, value=2, dedupe="missing-reach-out-%d" % int(dt.datetime.now().timestamp() // 3600),
        )
        intents = intent_store.read_jsonl(intent_store.INTENTS)
        if not intent_store.is_duplicate(intents, cand):
            intents.append(cand)
            intent_store.write_intents(intents)
    except Exception:
        pass


def main():
    now = dt.datetime.now()
    print(f"[Current date/time: {now.strftime('%A, %B %d, %Y, %I:%M %p')} local time.]")
    line = session_started_line(now)
    if line:
        print(line)
    print(presence_line(now))
    line = emotional_line(now)
    if line:
        print(line)
    try:
        import os
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from situation import situation_line
        print(situation_line() + " Look it up before asking or saying you don't know.")   # 2026-10-29: he said I only know what he tells me
    except Exception:
        pass
    print(focus_line())
    line = intent_line()
    if line:
        print(line)


if __name__ == "__main__":
    main()
