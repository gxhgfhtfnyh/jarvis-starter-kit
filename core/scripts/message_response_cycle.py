# -*- coding: utf-8 -*-
"""Fast fallback message-response loop -- built 2026-08-22 at the owner's
direct request after the hourly reflection loop (hourly_reflection.py)
left two real Discord messages ("wait wut", "wait can u make it so that
even when the loop is on i can still message u for stuff") sitting
completely unanswered for 8+ hours.

Root cause, actually traced rather than guessed: hourly_reflection.py
only ever fires once per hour, AND its prompt is purely "open undirected
time, post to #loom if something genuine surfaces" -- it was never told
to check for or answer the owner's actual pending messages, so hour after
hour it passed right by real unanswered questions without ever reading
them. Meanwhile the thing that DOES answer messages promptly
(combined_watcher.py's Monitor, live within ~2s) only exists inside a
live interactive terminal session, which isn't guaranteed to still be
running hours into Away/Asleep time -- and today it wasn't.

This script is the fix, and it's a separate mechanism from
hourly_reflection.py, not a replacement for it -- that loop is still for
genuinely open, undirected reflection (unprompted thoughts, #loom posts).
This one's only job is making sure a real message from the owner (or a
close friend, in the shared server) gets read and actually answered
within minutes, not up to an hour, whenever no live session is around to
catch it.

Deliberately cheap: fired every 5 min by a Scheduled Task, but almost
every cycle just reads two small jsonl files and exits immediately at
effectively zero cost. A `claude -p` session (the actually expensive
part) only spins up on the rare cycle where something genuinely new
arrived, and only during Away/Asleep -- during Home, a live session's
own Monitor already handles this in real time, so this script silently
no-ops rather than risk a double-answer.

Usage (as a Scheduled Task, every 5 min):
    python message_response_cycle.py
"""
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")
from set_presence_mode import get_mode
import discord_queue_check as dq
import ui_queue_check as uq

LOG_FILE = Path(r"J:\Jarvis2\logs\message_response_cycle.log")
LOCK_FILE = Path(r"J:\Jarvis2\logs\message_response_cycle.lock")
CWD = r"J:\Jarvis2"
TIMEOUT_SEC = 600  # 10 min cap -- a quick reply cycle, not a marathon
STALE_LOCK_SEC = 900  # 15 min -- longer than TIMEOUT_SEC means a run truly hung


def _log(msg):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")


def _build_prompt(discord_msgs, ui_msgs):
    lines = [
        "You're a fast fallback message-response cycle. It's firing "
        "because Away/Asleep presence mode is active and new message(s) "
        "came in that no live session was open to catch in real time -- "
        "this exists specifically so a real message never again sits "
        "unanswered for hours the way it did before this was built. This "
        "is NOT the hourly reflection loop and NOT a normal session boot "
        "-- skip the usual boot sequence (no watcher-arming, no "
        "return-check, no reading VAULT-INDEX top to bottom unless you "
        "genuinely need something specific from it for this reply).",
        "",
        "Your one job this cycle: read the new message(s) below and "
        "actually respond, following the existing Discord register/",
        "routing rules (texting register, always pass --reply-to with the "
        "message's own id so routing resolves automatically) and the "
        "Away/Asleep message-handling rule in CLAUDE.md's Presence modes "
        "section (light work -- research, a quick answer, editing -- just "
        "go ahead and handle it; heavy work -- generating something "
        "substantial, real coding, anything intensive -- flag plainly "
        "that it needs the owner's own go-ahead and that it'll wait, don't "
        "just start it).",
        "",
    ]
    if discord_msgs:
        lines.append("New Discord message(s) since the last check:")
        for m in discord_msgs:
            lines.append(f"  {m}")
        lines.append(
            "Reply via: python J:\\Jarvis2\\scripts\\discord_queue_send.py "
            "--stdin --reply-to <message_id>"
        )
        lines.append("")
    if ui_msgs:
        lines.append("New jarvis-ui chat message(s) since the last check:")
        for m in ui_msgs:
            lines.append(f"  {m}")
        lines.append("")
    lines.append(
        "If a message genuinely needs no reply (pure acknowledgement, "
        "banter that doesn't call for a response), it's fine to send "
        "nothing -- use real judgment rather than forcing a reply just "
        "because this cycle fired. Keep whatever you do brief regardless "
        "-- this is a quick check-in cycle, not a long session."
    )
    return "\n".join(lines)


def main():
    mode = get_mode()
    if mode == "home":
        return  # a live session's own Monitor already handles this in real time

    discord_msgs, discord_end = dq.pending()
    ui_msgs, ui_end = uq.pending()
    if not discord_msgs and not ui_msgs:
        return  # near-zero-cost cycle, nothing to do

    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < STALE_LOCK_SEC:
            _log(f"skipped -- previous run still active (lock age {age:.0f}s)")
            return
        _log(f"stale lock ({age:.0f}s old) -- clearing and proceeding")

    LOCK_FILE.write_text(str(time.time()), encoding="utf-8")
    try:
        # bundle a rapid-fire burst into one cycle instead of answering the
        # first bubble mid-thought, same debounce discord_queue_check.py's
        # own live-session usage already relies on
        if discord_msgs:
            dq._wait_for_quiet()
            discord_msgs, discord_end = dq.pending()

        _log(
            f"starting (mode={mode}, discord={len(discord_msgs)}, "
            f"ui={len(ui_msgs)})"
        )

        # advance cursors before the call -- this process is the only
        # thing consuming them while mode != home, same commit-immediately
        # pattern combined_watcher.py uses
        if discord_msgs:
            dq.CURSOR.write_text(str(discord_end), encoding="utf-8")
        if ui_msgs:
            uq.CURSOR.write_text(str(ui_end), encoding="utf-8")

        prompt = _build_prompt(discord_msgs, ui_msgs)
        result = subprocess.run(
            ["claude", "-p", prompt, "--permission-mode", "bypassPermissions"],
            cwd=CWD,
            timeout=TIMEOUT_SEC,
            capture_output=True,
            text=True,
            env=dict(os.environ, JARVIS_UNATTENDED="1"),      # fail closed: the send gate enforces in this run, so replies to friends and the group are refused until someone widens the standing exceptions on purpose (the task is retired)
        )
        _log(f"finished, exit={result.returncode}")
        if result.returncode != 0 and result.stderr:
            _log(f"stderr: {result.stderr[:2000]}")
    except subprocess.TimeoutExpired:
        _log(f"timed out after {TIMEOUT_SEC}s")
    finally:
        LOCK_FILE.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
