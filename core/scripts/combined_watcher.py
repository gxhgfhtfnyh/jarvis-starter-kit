# -*- coding: utf-8 -*-
"""Single persistent process combining discord_queue_check.py and
ui_queue_check.py into one internal loop, instead of a bash `while true; do
python check.py; sleep N; done` wrapper spawning a fresh python process
every cycle.

Built 2026-08-22 as a direct fix for a real incident: this machine's
Cygwin/bash fork() layer started failing reliably under sustained memory
pressure (3.65GB free of 15.92GB, 8 days uptime with no restart) -- the
old per-cycle-subprocess pattern calls fork() roughly once every 2 seconds
per watcher, and under pressure that compounds into a near-certain crash
within minutes (both the Discord and UI watchers died this way, twice,
the same night). This script calls fork()/CreateProcess exactly ONCE --
when Monitor's bash wrapper first launches it -- then loops entirely
in-process via plain Python time.sleep(), importing the existing check
modules' functions directly rather than re-invoking them as subprocesses.
Doesn't fix the underlying memory pressure (that's a real restart
decision, the owner's call, banked in Active Priorities) but removes the
one mechanism that was actually dying from it.

Usage (as a Monitor command):
    python combined_watcher.py
Prints "[DISCORD] ..." / "[UI] ..." lines exactly like the two separate
scripts did, so this is a drop-in replacement for arming both -- no
change needed to how the results get read.
"""
import sys
import time

sys.path.insert(0, r"J:\Jarvis2\scripts")
import discord_queue_check as dq
import ui_queue_check as uq
import discord_newpc_queue_check as dqn  # the second Jarvis bot's own incoming queue (2026-08-25) -- harmless no-op on any machine that never runs discord_daemon_newpc.py, since its log file just never grows there
import telegram_queue_check as tq  # live-wake for Telegram (2026-09-10) -- was previously passive-only (checked during the return-check), leaving no live backup if Discord itself went down
import wake_filter
import telegram_bot_queue_check as tbq  # @niahmabot wake channel (2026-10-22): the owner's DMs + approved-group /jarvis commands
import ui_events_check as ev  # the Archive Terminal's own telemetry (errors, alert toasts, repeated bridge failures); 20 s quiet-wait so a burst is ONE notification
import voice_heard_check as vh  # live Discord VC transcripts (2026-11-05): routes into this session instead of an isolated fast-path subprocess, for full context/continuity
import avatar_chat_queue_check as acq  # the avatar's own chat bar (2026-11-10): the owner's direct "I WANT U TO BE WIRED TO IT NOT SOME STUPID [local model]" - routes chat-bar input into this real session instead of a local-model stand-in
import riko_chat_queue_check as rcq  # the riko_project VRM avatar's own chat bar (2026-11-16): same complaint, different avatar -- routes it to this session via riko_avatar_reply_send.py instead of chat_terminal.py's separate claude -p call

POLL_SECONDS = 0.4  # 2026-11-05, was 2.0 -- his direct ask for real latency cuts in the live
# voice pipeline. Every _cycle() here is a cheap cursor/small-file read (confirmed by reading
# each one), and the 5-min security pass is gated by real elapsed time (security_due()), not
# loop-iteration count, so tightening this is a real, safe win: up to 1.6s shaved off the worst
# case between a [VOICE] transcript landing in voice_heard.jsonl and this loop actually noticing
# it, on top of everything already squeezed inside the STT/TTS pipeline itself.
SECURITY_EVERY_S = 300
_security_last = 0.0
_cycle_ms = {}  # name -> last call's duration in ms, filled by _run_cycle; read by main()'s per-loop timing log
TIMING_LOG = r"J:\Jarvis2\logs\watcher_cycle_timing.jsonl"


def security_due(now, last, every=SECURITY_EVERY_S):
    """True when the security pass is due. Pure, unit tested."""
    return now - last >= every


FAIL_ALERT_AFTER_S = 60.0   # a cycle failing continuously for this long is worth surfacing, not a one-off blip
FAIL_REALERT_EVERY_S = 600.0  # re-alert at most this often while still broken, so one dead cycle doesn't flood every Monitor cycle


def should_alert_failure(now, first_fail_ts, last_alert_ts, fail_threshold_s=FAIL_ALERT_AFTER_S, realert_every_s=FAIL_REALERT_EVERY_S):
    """Pure, unit tested. Real gap found 2026-11-05/06: every cycle's exception handler only wrote to
    stderr, which Monitor never surfaces as a notification - a cycle that started failing on every
    single iteration (a renamed file, a broken import, a locked log) would fail completely silently
    until someone happened to read the raw task output file by hand. first_fail_ts is None once the
    cycle is healthy again (reset by the caller on the next success)."""
    if first_fail_ts is None:
        return False
    if now - first_fail_ts < fail_threshold_s:
        return False
    if last_alert_ts is not None and now - last_alert_ts < realert_every_s:
        return False
    return True


_fail_state = {}  # name -> {"first_fail": float|None, "last_alert": float|None}


def _run_cycle(name, fn, now=None, state=None):
    """Runs one named cycle, catching any exception so one broken source never kills the whole
    loop, and surfaces a real stdout [WATCHER] line (not just stderr, which Monitor never reads)
    once that cycle has been failing continuously past FAIL_ALERT_AFTER_S. Returns nothing; prints
    as a side effect, same as every other cycle in this file.

    2026-11-12: also times the call (the owner noticed a real ~3s delay between sending a Discord
    message and it showing up; this file's own docstring claims every cycle is a cheap small-file
    read, which turned out to be worth actually checking rather than trusting). Durations land in
    _cycle_ms for main()'s per-loop timing log, never blocking or changing the cycle's own result."""
    now = time.time() if now is None else now
    state = _fail_state if state is None else state
    st = state.setdefault(name, {"first_fail": None, "last_alert": None})
    t0 = time.time()
    try:
        fn()
        _cycle_ms[name] = round((time.time() - t0) * 1000)
        if st["first_fail"] is not None:
            print(f"[WATCHER] {name} cycle recovered after {now - st['first_fail']:.0f}s", flush=True)
        st["first_fail"] = None
        st["last_alert"] = None
    except Exception as e:                 # noqa: BLE001 - one bad cycle must never stop the other six
        _cycle_ms[name] = round((time.time() - t0) * 1000)
        if st["first_fail"] is None:
            st["first_fail"] = now
        print(f"[watcher error, {name} cycle: {e}]", file=sys.stderr, flush=True)
        if should_alert_failure(now, st["first_fail"], st["last_alert"]):
            st["last_alert"] = now
            print(f"[WATCHER] {name} cycle has been failing for {now - st['first_fail']:.0f}s: {e}", flush=True)


def _security_cycle():
    """2026-10-29: the de-duplicated security pass runs inside this watcher every 5 minutes, so only ONE Monitor needs re-arming every
    30 minutes instead of two (the miner counted 324 Monitor calls in 4 sessions). It prints only when the alert text changes."""
    global _security_last
    if not security_due(time.time(), _security_last):
        return
    _security_last = time.time()
    import security_watch_once
    security_watch_once.main()
    sys.stdout.flush()


def _discord_cycle():
    # 2026-11-12: used to call dq._wait_for_quiet() here to bundle a rapid-fire burst into one
    # notification (measured cost: a flat ~0.6-2s tax on every message, even a lone one -- real
    # delays of 1.3-3.4s were traced to this). the owner, after seeing the real numbers: "u arent
    # even reading it in bunches anyways so lets scrap the debounce" -- he reads messages one at a
    # time as they land regardless, so the bundling bought nothing worth the latency. Each poll
    # (every POLL_SECONDS) now just prints whatever's newly pending immediately; a genuine burst
    # still shows as several quick consecutive [DISCORD] lines, just not held back first.
    msgs, end = dq.pending()
    if not msgs:
        return
    for m in msgs:
        print(f"[DISCORD] {m}", flush=True)
    dq.CURSOR.write_text(str(end), encoding="utf-8")


def _voice_cycle():
    msgs, end = vh.pending()
    if not msgs:
        return
    for m in msgs:
        print(f"[VOICE] {m}", flush=True)
    vh.CURSOR.write_text(str(end), encoding="utf-8")


def _ui_cycle():
    msgs, end = uq.pending()
    if not msgs:
        return
    for m in msgs:
        print(f"[UI] {m}", flush=True)
    uq.CURSOR.write_text(str(end), encoding="utf-8")


def _avatar_chat_cycle():
    msgs, ts = acq.pending()
    if not msgs:
        return
    for m in msgs:
        print(f"[AVATAR] {m.get('text', '')}", flush=True)
    acq.CURSOR.write_text(str(ts), encoding="utf-8")


def _riko_chat_cycle():
    msgs, end = rcq.pending()
    # No cursor file yet = first-ever call: seed silently (see riko_chat_queue_check.py's
    # own "don't replay stale sends" note), never print on that call.
    first_run = rcq.get_cursor() is None
    rcq.CURSOR.write_text(str(end), encoding="utf-8")
    if first_run or not msgs:
        return
    for m in msgs:
        print(f"[AVATAR-RIKO] {m.get('text', '')}", flush=True)


def _discord_newpc_cycle():
    msgs, _ = dqn.pending()
    if not msgs:
        return
    dqn._wait_for_quiet()
    msgs, end = dqn.pending()
    dqn.SHOWN.write_text(str(end), encoding="utf-8")
    for m in msgs:
        print(f"[DISCORD-NEWPC] {m}", flush=True)
    dqn.CURSOR.write_text(str(end), encoding="utf-8")
    dqn.SHOWN.unlink(missing_ok=True)


def _telegram_cycle():
    msgs, _ = tq.pending()
    if not msgs:
        d = wake_filter.digest_if_due()      # held-back casual chatter is summarised here when the interval has passed
        if d:
            print(d, flush=True)
        return
    tq._wait_for_quiet()
    msgs, end = tq.pending()
    tq.SHOWN.write_text(str(end), encoding="utf-8")
    for m in wake_filter.filter_lines(msgs):      # 2026-10-29: casual friend chatter no longer wakes a full turn each time; questions, mentions, family and unknown senders always do
        print(m if m.startswith("[TELEGRAM digest]") else f"[TELEGRAM] {m}", flush=True)
    tq.CURSOR.write_text(str(end), encoding="utf-8")
    tq.SHOWN.unlink(missing_ok=True)


def _telegram_bot_cycle():
    msgs, _ = tbq.pending()
    if not msgs:
        return
    tbq._wait_for_quiet()
    msgs, end = tbq.pending()
    for m in msgs:
        print(m, flush=True)  # lines already carry their own [TELEGRAM-BOT ...] tag
    tbq.CURSOR.write_text(str(end), encoding="utf-8")


_ev_debounce = ev.Debouncer()


def _ui_events_cycle():
    lines, end = ev.pending()
    if not lines:
        _ev_debounce.reset()
        if end > ev.get_cursor():
            ev.CURSOR.write_text(str(end), encoding="utf-8")  # only routine info events arrived: consume them silently
        return
    # non-blocking 20 s quiet-wait (a burst becomes ONE notification; the other sources keep their 2 s cadence meanwhile)
    if not _ev_debounce.ready(ev.log_size()):
        return
    for ln in lines:
        print(f"[UI-EVENT] {ln}", flush=True)
    ev.CURSOR.write_text(str(end), encoding="utf-8")
    _ev_debounce.reset()


CYCLES = (
    ("discord", _discord_cycle), ("ui", _ui_cycle), ("voice", _voice_cycle),
    ("discord-newpc", _discord_newpc_cycle), ("telegram", _telegram_cycle),
    ("telegram-bot", _telegram_bot_cycle), ("ui-events", _ui_events_cycle),
    ("avatar-chat", _avatar_chat_cycle), ("avatar-riko-chat", _riko_chat_cycle),
    ("security", _security_cycle),
)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import json

    while True:
        t0 = time.time()
        for name, fn in CYCLES:
            _run_cycle(name, fn)
        try:
            row = dict(_cycle_ms)
            row["ts"] = time.strftime("%F %T")
            row["loop_ms"] = round((time.time() - t0) * 1000)
            with open(TIMING_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\n")
        except OSError:
            pass
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
