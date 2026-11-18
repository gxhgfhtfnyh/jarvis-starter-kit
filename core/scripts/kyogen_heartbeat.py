# -*- coding: utf-8 -*-
"""A real, independent progress signal for Kyogen (op a_4e656055), built 2026-11-14 right after
the owner said "i am on rdp cause rn icant trust u to actually not stop working... until u can
100% confidently tell me u wont stop and ill come back to a lot of actual progress then i can
rest easy." Honest answer given in chat: I can't promise this exact Claude Code session never
goes quiet -- that would be an unverifiable claim. What I CAN do is build something that reports
real progress on its OWN schedule, completely decoupled from whether this chat session is alive
at all -- so he doesn't have to trust my word, he can just read what this independently-running
check actually found.

Run standalone (a Windows Scheduled Task, not tied to this Claude Code session): checks whether
the real swarm_daemon.py and discord_daemon_kyogen.py processes are actually alive, how fresh
their own log activity is, and the real current lesson count, then sends ONE real status line to
Discord via the existing send mechanism -- itself a separate, already-running daemon
(discord_daemon.py), not this script's own process staying alive.
"""
import datetime as dt
import json
import os
import subprocess
import sys

import psutil

import swarm_meta_review as mr

LESSONS_PATH = r"C:\LocalAI\swarm_memory\lessons.jsonl"
SWARM_DAEMON_LOG = r"J:\Jarvis2\logs\swarm_daemon_stdout.log"
KYOGEN_BOT_LOG = r"J:\Jarvis2\logs\discord_kyogen_daemon.log"
HEARTBEAT_LOG_PATH = r"J:\Jarvis2\logs\kyogen_heartbeat.jsonl"
STALE_AFTER_SECONDS = 1800  # 30 min -- longer than any single real cycle (the 30-min
# improvement-cycle cooldown itself) should ever take, so a genuinely stuck process still
# reports fresh right up until it's actually worth flagging.


def _process_alive(cmdline_substring):
    """Pure read of real OS process state. Returns True if any running process's command line
    contains the given substring."""
    for p in psutil.process_iter(["cmdline"]):
        try:
            cmdline = p.info.get("cmdline") or []
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if any(cmdline_substring in part for part in cmdline):
            return True
    return False


def _log_freshness_seconds(path, now=None):
    """Pure-ish (one real file stat). Returns seconds since the log file was last modified, or
    None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    now = now or dt.datetime.now().timestamp()
    return now - os.path.getmtime(path)


def gather_status():
    """Real entry point: collects every fact fresh, never caches or assumes. Returns a dict,
    never a pre-written sentence -- compose_message() is the only place that turns facts into
    words, so the facts themselves stay independently checkable."""
    lessons = mr.load_recent_lessons(LESSONS_PATH, n=10000)
    return {
        "daemon_alive": _process_alive("swarm_daemon.py"),
        "daemon_log_age_s": _log_freshness_seconds(SWARM_DAEMON_LOG),
        "bot_alive": _process_alive("discord_daemon_kyogen.py"),
        "bot_log_age_s": _log_freshness_seconds(KYOGEN_BOT_LOG),
        "lesson_count": len(lessons),
        "checked_at": dt.datetime.now().isoformat(timespec="seconds"),
    }


def compose_message(status, stale_after=STALE_AFTER_SECONDS):
    """Pure. Turns the real facts into one honest line -- flags staleness explicitly rather
    than reporting 'alive' when the process exists but the log shows no recent real activity
    (a hung process is not the same thing as a working one)."""
    parts = []
    if status["daemon_alive"]:
        age = status["daemon_log_age_s"]
        if age is not None and age > stale_after:
            parts.append("daemon process up but quiet for %dmin" % (age // 60))
        else:
            parts.append("daemon active")
    else:
        parts.append("daemon NOT running")
    if status["bot_alive"]:
        age = status["bot_log_age_s"]
        if age is not None and age > stale_after:
            parts.append("bot process up but quiet for %dmin" % (age // 60))
        else:
            parts.append("bot active")
    else:
        parts.append("bot NOT running")
    parts.append("%d lessons" % status["lesson_count"])
    return "kyogen check: " + ", ".join(parts)


def _last_logged(log_path):
    """Pure-ish (one real file read). The last real heartbeat this script itself logged, or
    None if there isn't one yet -- never raises on a missing/corrupt file."""
    try:
        with open(log_path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        return json.loads(lines[-1]) if lines else None
    except (OSError, ValueError, IndexError):
        return None


def has_changed(status, last, stale_after=STALE_AFTER_SECONDS):
    """Pure. True when something a human would actually want to hear about changed since the
    last real heartbeat -- found live 2026-11-14 (his direct word: "its spammimg our dms"):
    this used to send on every scheduled run regardless, repeating the exact same "still fine"
    status every ~20 min for hours. Alive/dead flips and staleness crossing the real threshold
    both count; lesson_count does NOT -- it grows routinely every cycle and would make this
    fire almost as often as before, defeating the fix."""
    if last is None:
        return True  # nothing logged yet -- a real first report is always worth sending
    if status["daemon_alive"] != last.get("daemon_alive"):
        return True
    if status["bot_alive"] != last.get("bot_alive"):
        return True
    was_stale_daemon = (last.get("daemon_log_age_s") or 0) > stale_after
    now_stale_daemon = (status["daemon_log_age_s"] or 0) > stale_after
    if was_stale_daemon != now_stale_daemon:
        return True
    was_stale_bot = (last.get("bot_log_age_s") or 0) > stale_after
    now_stale_bot = (status["bot_log_age_s"] or 0) > stale_after
    if was_stale_bot != now_stale_bot:
        return True
    return False


def send_to_discord(text):
    """Real send via the existing, already-running discord_daemon.py outbox mechanism --
    completely independent of this script's own process or this Claude Code session."""
    proc = subprocess.run(
        [sys.executable, r"J:\Jarvis2\scripts\discord_queue_send.py", "--stdin"],
        input=text, capture_output=True, text=True, timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def main():
    status = gather_status()
    message = compose_message(status)
    last = _last_logged(HEARTBEAT_LOG_PATH)
    returncode = None
    sent = has_changed(status, last)
    if sent:
        returncode, stdout, stderr = send_to_discord(message)
    with open(HEARTBEAT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({**status, "message": message, "sent": sent, "send_returncode": returncode}) + "\n")
    print(message if sent else "(unchanged, not sent) " + message)


if __name__ == "__main__":
    main()
