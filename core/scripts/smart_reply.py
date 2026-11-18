# -*- coding: utf-8 -*-
"""Routes a reply to the RIGHT channel automatically, by reading the literal
tag off the triggering event line - built 2026-11-10 after repeatedly (4x
in one session) replying to a [DISCORD] message via avatar_reply_send.py (the
avatar chat-bar channel) by mistake, which silently drops the reply with no
error. A saved memory note alone did not stop the repeat under fast-moving,
interleaved-channel conversation. This makes the channel choice mechanical
instead of a judgment call: pass the raw event line (or just the tag) and
the text, and it picks the right underlying script for you.

Usage:
    python smart_reply.py "[DISCORD] (id 123456789...) some text" --text "reply" [--kind card|symbol|diagram|progress]
    python smart_reply.py "[AVATAR] some text" --text "reply" [--kind ...]
    python smart_reply.py --tag discord --id 123456789 --text "reply"
    python smart_reply.py --tag avatar --text "reply"
    echo "reply text" | python smart_reply.py "[DISCORD] (id 123) ..." --stdin
"""
import argparse
import re
import subprocess
import sys

SCRIPT_DIR = r"J:\Jarvis2\scripts"


def parse_tag_and_id(event_line):
    m = re.match(r"\s*\[(DISCORD|AVATAR|TELEGRAM)\]\s*(?:\(id\s+(\S+?)[,)])?", event_line or "")
    if not m:
        return None, None
    tag = m.group(1).upper()
    msg_id = m.group(2)
    if msg_id and msg_id.endswith(","):
        msg_id = msg_id[:-1]
    return tag, msg_id


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("event_line", nargs="?", help="the raw event line, e.g. '[DISCORD] (id 123) text'")
    ap.add_argument("--tag", choices=["discord", "avatar", "telegram"], help="explicit channel override")
    ap.add_argument("--id", dest="msg_id", help="explicit Discord message id to reply to (overrides parsed one)")
    ap.add_argument("--text", help="the reply text")
    ap.add_argument("--stdin", action="store_true", help="read reply text from stdin instead of --text")
    ap.add_argument("--kind", choices=["card", "symbol", "diagram", "progress"], default="card",
                     help="avatar-channel only: spawn kind")
    a = ap.parse_args(argv)

    text = sys.stdin.read().strip() if a.stdin else a.text
    if not text:
        print("no reply text given", file=sys.stderr)
        return 1

    tag = (a.tag or "").upper() or None
    msg_id = a.msg_id
    if tag is None:
        parsed_tag, parsed_id = parse_tag_and_id(a.event_line or "")
        tag = parsed_tag
        msg_id = msg_id or parsed_id

    if tag is None:
        print("could not determine channel - pass --tag or a proper '[DISCORD]'/'[AVATAR]' event line",
              file=sys.stderr)
        return 1

    if tag == "DISCORD":
        cmd = [sys.executable, f"{SCRIPT_DIR}\\discord_queue_send.py", "--stdin"]
        if msg_id:
            cmd += ["--reply-to", msg_id]
        proc = subprocess.run(cmd, input=text, text=True, encoding="utf-8")
        return proc.returncode
    elif tag == "AVATAR":
        cmd = [sys.executable, f"{SCRIPT_DIR}\\avatar_reply_send.py", "--stdin", "--kind", a.kind]
        proc = subprocess.run(cmd, input=text, text=True, encoding="utf-8")
        return proc.returncode
    elif tag == "TELEGRAM":
        print("TELEGRAM routing not wired up in smart_reply.py yet - use the telegram script directly",
              file=sys.stderr)
        return 1
    else:
        print(f"unknown tag {tag!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
