# -*- coding: utf-8 -*-
"""
Built 2026-08-23. SessionStart already auto-launches the daemons (Discord,
Telegram, voice, avatar) via plain command hooks, but the actual "arm the
live UI+Discord/security-alert watchers and go read recent Discord/UI logs
for context" part only ever happened because either the model chose to
follow CLAUDE.md's written Startup Sequence section on its own, or because
the owner (or the session watchdog's seeded prompt) explicitly asked for it
in the first message. A genuinely fresh "Start Jarvis" launch with no such
explicit ask had no structural guarantee this happens - same "a written
instruction isn't a mechanism" gap already fixed for the mood-hook,
tone-drift, and web-nav-protocol cases tonight.

Injects a short additionalContext directive at session boot pointing back
at CLAUDE.md's own Startup Sequence section (not duplicating its content
here, so there's one source of truth) rather than relying on the model to
remember to look at it unprompted.

**Real incident, 2026-08-23, fixed same day**: this hook originally fired
unconditionally for every session start, including narrow one-shot `claude
-p` invocations (the morning inbox check, scratch cleanup) that have no
business arming competing watchers. One such one-shot followed this hook's
own directive, found the real continuous session's `combined_watcher.py`
running, wrongly concluded it was orphaned, killed it, and armed its own
replacement - a real collision, caught and corrected the same morning. Now
skips entirely when `JARVIS_HEADLESS_CYCLE=1` is set (the same env var
`hourly_reflection.py` and `run_scheduled_prompt.ps1` already set for their
own one-shot claude -p calls) - narrow, scoped invocations should just do
their one job and exit, not take on watcher-ownership responsibilities.

Usage (as a SessionStart hook, alongside the existing daemon-launcher
hooks):
    python startup_checklist_hook.py
"""
import json
import os
import sys
import time

# 2026-11-14, his direct ask after catching a real mistake ("a few hours ago" when the real gap
# was ~11h, spanning a sleep): I already get the real current date/time every turn, what I
# didn't have was a real anchor for how long THIS session has actually been running, so a
# relative-time claim like "earlier tonight" was coming from fuzzy memory, not a number. Written
# here (SessionStart), read and shown every turn by current_time_hook.py.
SESSION_START_PATH = r"J:\Jarvis2\logs\session_started.json"

# "resume"/"clear"/"compact"/"fork" are the SAME lived session continuing, not a new one -- only
# a genuine "startup" (or no file existing yet at all) should reset the anchor. Resetting on
# compact specifically would be actively wrong: that's exactly the kind of multi-hour, multi-
# compaction stretch this is meant to track elapsed time across, not reset every time it happens.
RESET_SOURCES = {"startup"}


def maybe_record_session_start(source, now=None, path=SESSION_START_PATH):
    """Pure-ish (one real file read/write). Returns the real started_at epoch -- either the
    existing one (resume/clear/compact/fork, or a startup with no prior record to compare) or a
    freshly-written one (a genuine new "startup")."""
    now = now if now is not None else time.time()
    existing = None
    try:
        with open(path, encoding="utf-8") as f:
            existing = json.load(f).get("started_at")
    except (OSError, ValueError, KeyError, TypeError):
        existing = None
    if existing is not None and source not in RESET_SOURCES:
        return existing
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"started_at": now, "source": source}, f)
    return now

REMINDER = (
    "[Session just started. Before anything else, run CLAUDE.md's own "
    "Startup Sequence section: read VAULT-INDEX.md, check yesterday's daily "
    "note, scan Active Priorities.md, arm the combined UI+Discord watcher "
    "and the security-alert watcher (steps 3b-3e) IF one isn't already "
    "running (check for an existing combined_watcher.py process first - "
    "never assume one is orphaned just because you can't see its origin), "
    "and do the full return-check (Discord/Gmail/etc.) if this looks like "
    "the owner actually returning rather than a bare unattended relaunch. Do "
    "this even if the first user message doesn't explicitly ask for it.]"
)


def main():
    if os.environ.get("JARVIS_HEADLESS_CYCLE") == "1":
        return
    try:
        hook_input = json.loads(sys.stdin.read() or "{}")
    except (ValueError, TypeError):
        hook_input = {}
    # Real bug caught live 2026-11-14: defaulting a missing/unclear `source` to "startup" was
    # exactly backwards -- it meant ANY hook firing that didn't cleanly report its source reset
    # the anchor, and the whole point of this feature is surviving exactly that kind of
    # ambiguity. "unknown" is never in RESET_SOURCES, so an uncertain source now safely
    # preserves whatever's already recorded instead of assuming the riskier "definitely a real
    # new session" interpretation.
    # path=SESSION_START_PATH explicitly, not the bound default -- the exact early-binding
    # gotcha caught repeatedly tonight (kyogen_terminal.py, swarm_working_memory.py,
    # current_time_hook.py's own es.load_state(es.STATE_PATH) pattern): a bare call here would
    # use whatever SESSION_START_PATH was at function-definition time, so a test (or anything
    # else) monkeypatching the module attribute would silently have no effect.
    maybe_record_session_start(hook_input.get("source") or "unknown", path=SESSION_START_PATH)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": REMINDER,
        }
    }))


if __name__ == "__main__":
    main()
    sys.exit(0)
