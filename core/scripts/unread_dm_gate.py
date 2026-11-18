# -*- coding: utf-8 -*-
"""Unread-DM Stop gate (2026-11-12, "make sure the watcher is always sending dc msgs to terminal").

Root cause of the original complaint: inbox_hook.py (PostToolUse) already delivers a new Discord DM at the next
tool-call boundary, and the Monitor tail on combined_watcher_live.log delivers one live -- but the Monitor has a
hard 30-minute cap and silently lapses if not re-armed, and there is a real gap even without that: a DM that
arrives strictly between my last tool call and the Stop event has no tool-call boundary left in the turn for
inbox_hook to catch it on. Fixing the Monitor habit does not close that gap; a structural check does.

This hook runs at Stop, peeks (never advances) inbox_hook.py's own cursor against discord_incoming.jsonl's current
size, and -- if the owner has DM content past that cursor -- raises a tier-1 intent exactly like
delivery_check_gate.py does. It never blocks by itself: keep_going.py (later in the same Stop array) reads
intents.jsonl fresh and holds the turn open, forcing a continue where the very next tool call lets inbox_hook.py
do the actual delivery. This makes the guarantee hook-based instead of Monitor-based: the Monitor stays useful as
a live nice-to-have, but is no longer the only thing standing between a message and me seeing it.

Testable core: find_unread_dm_ids(chunk_bytes, owner).
Kill switch: logs/unread_dm_gate_off.txt."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402

LOGS = os.path.join(os.path.dirname(HERE), "logs")
LOG = os.path.join(LOGS, "discord_incoming.jsonl")
CUR = os.path.join(LOGS, "inbox_hook_cursor.txt")
OFF = os.path.join(LOGS, "unread_dm_gate_off.txt")
OWNER = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it


def find_unread_dm_ids(chunk_bytes, owner=OWNER):
    """Pure. Message ids in `chunk_bytes` (raw bytes past the cursor) that are DMs from `owner`."""
    ids = []
    for line in chunk_bytes.decode("utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        if m.get("author_id") == owner and m.get("source") == "dm":
            ids.append(m.get("id"))
    return ids


def _unread_chunk():
    """(bytes or None): content past the saved cursor, or None if nothing to check (missing files, cursor caught up)."""
    try:
        size = os.path.getsize(LOG)
    except OSError:
        return None
    try:
        cur = int(open(CUR, encoding="utf-8").read().strip())
    except Exception:
        return None  # no cursor yet means inbox_hook hasn't run this session; let it set the baseline first
    if cur >= size:
        return None
    with open(LOG, "rb") as f:
        f.seek(cur)
        return f.read(size - cur)


def _raise_unread_intent(ids):
    import hashlib

    import intent_store

    dedupe = "unread-dm-%s" % hashlib.sha1(",".join(str(i) for i in ids).encode()).hexdigest()[:10]
    what = (
        "UNREAD DM: %d message(s) from the owner landed after your last tool call and were never surfaced "
        "(ids: %s). Make a tool call now (even just `python inbox_hook.py` logic will run on your next one "
        "automatically) so inbox_hook.py can deliver it, then reply." % (len(ids), ", ".join(str(i) for i in ids[-5:]))
    )
    cand = intent_store.new_intent("unread_dm", what, tier=1, value=4, dedupe=dedupe, from_event="unread_dm_gate")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(OFF):
            return
        chunk = _unread_chunk()
        if not chunk:
            return
        ids = find_unread_dm_ids(chunk)
        if ids:
            _raise_unread_intent(ids)
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
