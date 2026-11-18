# -*- coding: utf-8 -*-
"""
Checks whether discord_daemon.py's incoming log holds a DM from the owner that
the terminal session hasn't processed yet. Same mechanism as
ui_queue_check.py for the jarvis-ui chat watcher (2026-08-16, the owner:
"something similar to the jarvis ui chat watcher for the dms so the message
gets sent to u directly from discord") -- a session-side Monitor arming
`until python discord_queue_check.py; do sleep 2; done` wakes Jarvis within
seconds of a real Discord DM.

Exit 0 (and print the pending messages) when there's something new;
exit 1 when there isn't. `--ack` advances the cursor after processing.
Cursor = byte offset into discord_incoming.jsonl, stored in
discord_queue_cursor.txt.

Burst debounce (2026-08-16, the owner: "u gotta be able to handle multiple
texts at a time cause uk how i like to text"): a real person sending several
quick bubbles in a row shouldn't wake Jarvis after just the first one, mid-
thought. The moment a new message is seen, this waits for a short quiet gap
(no further new message) before actually returning -- so a rapid-fire burst
collects into one notification instead of firing once per bubble. Costs
nothing when only one message ever arrives (returns as soon as the gap
passes, no artificial floor beyond that).

2026-11-12: the quiet gap used to be 2.0s, which meant every single message
-- even a lone one -- paid a flat ~2s tax before ever reaching the live
session (measured live: discord cycle duration 2014ms, matching almost
exactly). the owner noticed ("i realize it takes like 3 seconds... just get
rid of the delay") and confirmed he wants it snappier. Tightened to 0.6s
(poll granularity tightened alongside it, 0.4s -> 0.15s, so the shorter
window still gets several checks rather than one coarse one) -- real
back-to-back texting bubbles land well inside 0.6s of each other, so burst
bundling still works, a lone message now only waits ~0.6s instead of ~2s.
"""
import json
import sys
import time
from pathlib import Path

INCOMING_LOG = Path(r"J:\Jarvis2\logs\discord_incoming.jsonl")
CURSOR = Path(r"J:\Jarvis2\logs\discord_queue_cursor.txt")
SHOWN = Path(r"J:\Jarvis2\logs\discord_queue_shown.txt")
DEBOUNCE_SECONDS = 0.6
DEBOUNCE_POLL_SECONDS = 0.15
# 2026-08-16: the group channel means "quiet" may never actually arrive --
# several people chatting keeps resetting the debounce indefinitely. A hard
# cap guarantees a wake within this long even mid-burst, so Jarvis stays
# present in a busy conversation instead of silently falling behind it.
MAX_WAIT_SECONDS = 6.0


def get_cursor():
    try:
        return int(CURSOR.read_text().strip())
    except (OSError, ValueError):
        return 0


def pending():
    if not INCOMING_LOG.exists():
        return [], 0
    size = INCOMING_LOG.stat().st_size
    cur = get_cursor()
    if cur > size:  # log truncated/cleared
        cur = 0
    msgs = []
    with open(INCOMING_LOG, "r", encoding="utf-8") as f:
        f.seek(cur)
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = e.get("text", "")
            attachments = e.get("attachments") or []
            if attachments:
                text = (text + " " if text else "") + " ".join(f"[attachment: {a}]" for a in attachments)
            # surface the message's own id (to reply-to it later via
            # discord_queue_send.py --reply-to) and whether it's itself a
            # reply to something -- 2026-08-16, the owner asked directly
            # whether Jarvis could see this
            prefix = f"(id {e.get('id')}"
            if e.get("reply_to"):
                prefix += f", replying to {e['reply_to']}"
            # server-channel messages (2026-08-16) can be from anyone Discord
            # lets into that channel, not just the owner -- surface who's
            # talking. Same for a close friend DM'ing directly (2026-08-16,
            # "dm_friend" source) -- only a plain "dm" (the owner himself)
            # stays unlabeled.
            if e.get("source") in ("guild", "dm_friend"):
                prefix += f", from {e.get('author_name', 'unknown')}"
            prefix += ") "
            # task capture (discord_capture.py): the daemon already added "add: ..." DMs from the owner as cards and replied,
            # so tell the session not to add it a second time
            try:
                if e.get("source") == "dm":
                    from discord_capture import parse_capture
                    if parse_capture(e.get("text", "")):
                        text += "  [AUTO-CAPTURED: the daemon already added this to Active Priorities and replied. Do NOT add it again.]"
            except Exception:
                pass
            msgs.append(prefix + text)
        end = f.tell()
    return msgs, end


def _wait_for_quiet():
    """Once something is pending, keep watching the log's size until it's
    been unchanged for DEBOUNCE_SECONDS -- collects a whole rapid-fire burst
    before the caller re-reads pending() for the final message list. Capped
    at MAX_WAIT_SECONDS total so a genuinely continuous group conversation
    (which may never go quiet) can't stall this indefinitely."""
    started = time.time()
    last_size = INCOMING_LOG.stat().st_size if INCOMING_LOG.exists() else 0
    last_change = time.time()
    while time.time() - last_change < DEBOUNCE_SECONDS:
        if time.time() - started >= MAX_WAIT_SECONDS:
            break
        time.sleep(DEBOUNCE_POLL_SECONDS)
        size = INCOMING_LOG.stat().st_size if INCOMING_LOG.exists() else last_size
        if size != last_size:
            last_size = size
            last_change = time.time()


def main():
    if "--ack" in sys.argv:
        # Real race, 2026-08-16 (the owner: "u cant just ignore messages when u
        # are generating the answer for another one" -- a busy group channel
        # exposed it): this used to call pending() fresh and commit whatever
        # it found *at that moment*, including any message that landed in the
        # gap between the plain check (which prints and notifies) and this
        # separate ack invocation -- silently marking it read without ever
        # having shown it. Now --ack only ever commits the exact offset the
        # check call already printed and recorded in SHOWN, never a fresh
        # scan, so it can't advance past something that was never surfaced.
        if not SHOWN.exists():
            return 0  # nothing was shown since the last ack -- nothing to commit
        end = SHOWN.read_text().strip()
        CURSOR.write_text(end, encoding="utf-8")
        SHOWN.unlink(missing_ok=True)
        print(f"cursor -> {end}")
        return 0
    msgs, _ = pending()
    if not msgs:
        return 1
    _wait_for_quiet()
    msgs, end = pending()  # re-read: the burst may have grown during the quiet-wait
    SHOWN.write_text(str(end), encoding="utf-8")
    for m in msgs:
        print(f"[DISCORD] {m}")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
