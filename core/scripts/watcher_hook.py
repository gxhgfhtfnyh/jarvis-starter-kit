# -*- coding: utf-8 -*-
"""PostToolUse watcher hook (2026-11-15, replacing the live tail -F Monitor after the owner caught
159 leftover zombie tail.exe processes -- every re-arm all night left one behind because the
Monitor's own "expired" notification never actually meant the underlying process died).

Mirrors inbox_hook.py's exact byte-offset-cursor pattern, but reads combined_watcher_live.log
instead of discord_incoming.jsonl. After every tool call, if combined_watcher.py (the real
persistent daemon, pid-checked separately) has written new lines since this hook last looked,
print them as hookSpecificOutput.additionalContext -- same near-zero latency as inbox_hook.py
already gets for Discord DMs, but with zero live subscription, zero re-arming, and zero process
leak risk, since this hook does no I/O beyond one file read per tool call.

Uses its OWN cursor file (logs/watcher_hook_cursor.txt), separate from any Monitor's tail -F.
Fast path: file size equals cursor -> exit without output. First run sets the cursor to the
current end. Never raises."""
import json
import os
import sys

LOG = r"J:\Jarvis2\logs\combined_watcher_live.log"
CUR = r"J:\Jarvis2\logs\watcher_hook_cursor.txt"
OFF = r"J:\Jarvis2\logs\watcher_hook_off.txt"  # kill switch


def is_subagent_call(hook):
    """True when the PostToolUse input comes from a sub-agent's tool call."""
    return isinstance(hook, dict) and bool(hook.get("agent_id") or hook.get("agent_type"))


def main():
    try:
        raw = sys.stdin.read()
        try:
            hook = json.loads(raw) if raw.strip() else {}
        except Exception:
            hook = {}
        if is_subagent_call(hook):
            return
        if os.path.exists(OFF):
            return
        if not os.path.exists(LOG):
            return
        size = os.path.getsize(LOG)
        try:
            cur = int(open(CUR).read().strip())
        except Exception:
            cur = size
            open(CUR, "w").write(str(size))
            return
        if cur > size:  # log rotated/truncated
            cur = size
            open(CUR, "w").write(str(size))
        if cur == size:
            return
        with open(LOG, "rb") as f:
            f.seek(cur)
            chunk = f.read(size - cur)
        open(CUR, "w").write(str(size))
        lines = [ln for ln in chunk.decode("utf-8", errors="ignore").splitlines() if ln.strip()]
        if lines:
            text = ("[WATCHER] new line(s) from combined_watcher_live.log since you last checked "
                    "(may duplicate something already seen another way; act once):\n" + "\n".join(lines[-20:]))
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}))
    except Exception:
        return


if __name__ == "__main__":
    main()
