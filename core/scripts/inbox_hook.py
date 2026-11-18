# -*- coding: utf-8 -*-
"""PostToolUse inbox hook (2026-10-29, research round 4, problem 5 stale messages).
After every tool call, if the owner has sent a new Discord DM since the last time this hook looked, print it as
hookSpecificOutput.additionalContext so I read it at the next tool boundary instead of at the end of a long turn.
Uses its OWN byte-offset cursor (logs/inbox_hook_cursor.txt), separate from the watcher's, so it never steals messages from
discord_queue_check.py; the same message may therefore also arrive through the watcher (expected duplicate; reply once).
Fast path: file size equals cursor -> exit without output. First run sets the cursor to the current end. Never raises.
Experiment: compare messages answered over 120 s per day before/after (scoreboard reply_over_120s)."""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402

ROOT = Path(os.environ.get("HARNESS_ROOT") or Path(HERE).parent).resolve()
LOGS = Path(os.environ.get("HARNESS_LOGS") or ROOT / "logs").resolve()
LOG = str(LOGS / "discord_incoming.jsonl")
CUR = str(LOGS / "inbox_hook_cursor.txt")
OWNER = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it
OFF = str(LOGS / "inbox_hook_off.txt")  # kill switch


def is_subagent_call(hook):
    """True when the PostToolUse input comes from a sub-agent's tool call (Claude Code adds agent_id / agent_type in that case)."""
    return isinstance(hook, dict) and bool(hook.get("agent_id") or hook.get("agent_type"))


def main():
    try:
        raw = sys.stdin.read()  # hook JSON
        try:
            hook = json.loads(raw) if raw.strip() else {}
        except Exception:
            hook = {}
        if any("agent" in str(k).lower() for k in hook):  # evidence of the real field names for sub-agent calls (keys only)
            try:
                open(r"J:\Jarvis2\logs\inbox_hook_agent_keys.json", "w").write(json.dumps(sorted(hook.keys())))
            except Exception:
                pass
        if is_subagent_call(hook):  # 2026-10-29: the researcher agent received my DM notice + scope-lock text through this hook and had to refuse it; sub-agents must never see my inbox
            return
        try:  # keep the field names of the last hook input for debugging (keys only, never values)
            open(r"J:\Jarvis2\logs\inbox_hook_keys.json", "w").write(json.dumps(sorted(hook.keys())))
        except Exception:
            pass
        try:  # 2026-10-29: every tool call is proof the live session is working, so autopilot/fallback never start beside a long turn
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from heartbeat import touch
            touch("claude_session", r"J:\Jarvis2\logs\heartbeats")
        except Exception:
            pass
        if os.path.exists(OFF):
            return
        size = os.path.getsize(LOG)
        try:
            cur = int(open(CUR).read().strip())
        except Exception:
            cur = size
            open(CUR, "w").write(str(size))
            return
        if cur > size:  # log rotated: persist the reset, otherwise every later call resets again and new messages are never read
            cur = size
            open(CUR, "w").write(str(size))
        if cur == size:
            return
        with open(LOG, "rb") as f:
            f.seek(cur)
            chunk = f.read(size - cur)
        open(CUR, "w").write(str(size))
        msgs = []
        for line in chunk.decode("utf-8", errors="ignore").splitlines():
            try:
                m = json.loads(line)
            except Exception:
                continue
            if m.get("author_id") == OWNER and m.get("source") == "dm":
                msgs.append("id %s: %s" % (m.get("id"), (m.get("text") or "[attachment]")[:300].replace("\n", " ")))
        if msgs:
            cur = ""
            try:  # scope lock (2026-10-29): show the current task at the exact moment of interruption
                for ln in open(r"J:\Jarvis2\logs\focus.md", encoding="utf-8").read().splitlines():
                    if ln.startswith("CURRENT:"):
                        cur = ln[:300]
            except Exception:
                pass
            text = ("[NEW DISCORD DM from the owner arrived while you were working; read it now and reply FIRST before continuing "
                    "(the watcher may deliver it again; reply only once)]\n" + "\n".join(msgs[-5:]) +
                    "\n[SCOPE LOCK] " + (cur or "CURRENT task: see focus.md") +
                    "\nRule: a CORRECTION applies to CURRENT. A QUESTION: answer, then return. A NEW idea or task is PARKED (add to the PARKED line "
                    "of focus.md and say so in one casual sentence) unless he says stop or switch. Do the classification silently and answer like a friend in "
                    "plain casual texting, no template, no bullet lists unless he asked for a list; then keep working on the current task.")
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
    except Exception:
        return


if __name__ == "__main__":
    main()
