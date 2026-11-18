# -*- coding: utf-8 -*-
"""
Say one short thing to the owner in LOOM'S OFFICE (the Archive Terminal chat, formerly COMMS).

Since 2026-10-15 nothing from the terminal is mirrored into the chat automatically (the owner: "i feel like im reading into your
inner thoughts and it feels weird"): only what is sent on purpose through this script (or discord_queue_send.py for Discord) reaches him.
In the office a short line appears on the speech card on my desk; a long one is typed out on the typewriter as a sheet of paper.
For an actual letter (a proper subject, a report, something to keep) use office_mail.py instead.

    python J:\\Jarvis2\\scripts\\office_say.py "short message"
    python J:\\Jarvis2\\scripts\\office_say.py --stdin   < text   (use this when the text has quotes)

comms_say.py is the same script under its old name. Texting register, short, same as Discord. Writes one
{"role": "assistant", "source": "comms"} line to jarvis_ui_chat.jsonl, which the office tails. Unknown flags are refused
(never queue a flag as a live message).
"""
import json
import os
import sys
import time
from pathlib import Path

CHAT_LOG = Path(os.environ.get("JARVIS_UI_CHAT_LOG") or r"J:\Jarvis2\logs\jarvis_ui_chat.jsonl")
MAX_CHARS = 8000


def say(text, log=None):
    text = str(text or "").strip()
    if not text:
        raise ValueError("nothing to send")
    text = text[:MAX_CHARS]
    path = Path(log) if log else CHAT_LOG
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.time(), "role": "assistant", "text": text, "source": "comms"}, ensure_ascii=False) + "\n")
    return text


def main(argv):
    args = list(argv[1:])
    lint_ok = "--lint-ok" in args
    if lint_ok:
        args = [a for a in args if a != "--lint-ok"]
    if args and args[0] == "--stdin":
        try:
            sys.stdin.reconfigure(encoding="utf-8")
        except Exception:
            pass
        text = sys.stdin.read()
    elif args and not args[0].startswith("--"):
        text = " ".join(args)
    else:
        print("usage: office_say.py \"message\"   or   office_say.py --stdin < text   [--lint-ok]", file=sys.stderr)
        return 2
    if not lint_ok:
        # 2026-11-12: this channel had zero autonomy-lint coverage while discord_queue_send.py's has caught real
        # ask-without-proof/narration slips all night -- the Archive Terminal was a real, previously-unaudited
        # bypass for exactly that. Reuses the same lint, never duplicated.
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from discord_queue_send import lint_or_exit

        lint_or_exit(text)
    try:
        sent = say(text)
    except ValueError as e:
        print(f"refused: {e}", file=sys.stderr)
        return 2
    print(f"sent to the office ({len(sent)} chars)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
