# -*- coding: utf-8 -*-
"""Shared conversation history for Kyogen across the Discord bot and the terminal harness (op
a_4e656055), split out 2026-11-14 after the owner's direct ask: "I NEED YOU TO MAKE SURE THE DC
AND THE TERMINAL IS SYNCED AND ARE THE SAME." Real evidence of the gap: the same two questions
("hi", "who are you?") asked in each interface produced two completely independent
conversations with different replies, because each interface kept its own history with no
shared state at all -- `discord_daemon_kyogen.py`'s per-channel history never reached the
terminal, and the terminal's in-memory `history` list was lost the moment it closed.

This module holds the one real persisted history file and the one shared conversation key both
interfaces read/write, so switching between them continues the same real conversation instead of
two disconnected ones. No Discord dependency here on purpose -- the terminal harness exists
specifically so a conversation with Kyogen doesn't require the Discord client/library at all; it
only needs to agree with the Discord bot on a file format and one well-known key.
"""
import collections
import json
from pathlib import Path

LOG_DIR = Path(r"J:\Jarvis2\logs")
HISTORY_FILE = LOG_DIR / "discord_kyogen_chat_history.json"
MAX_TURNS = 10
# the owner's real Kyogen DM channel id -- the one actual ongoing conversation. The terminal has
# no channel concept of its own, so it reads/writes under this exact same key rather than
# inventing a separate one that would just be a THIRD disconnected history.
SHARED_KEY = 1570870402769485844


def load_history(path=HISTORY_FILE):
    """Returns a channel_id(int) -> deque(maxlen=MAX_TURNS*2) dict loaded from disk, or an
    empty defaultdict if the file doesn't exist or is corrupt (never crashes startup over a
    bad history file)."""
    result = collections.defaultdict(lambda: collections.deque(maxlen=MAX_TURNS * 2))
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    for channel_id_str, turns in raw.items():
        try:
            channel_id = int(channel_id_str)
        except ValueError:
            continue
        result[channel_id] = collections.deque(turns, maxlen=MAX_TURNS * 2)
    return result


def save_history(history, path=HISTORY_FILE):
    """Writes the current per-channel history to disk, keyed by channel_id as a string (JSON
    object keys must be strings)."""
    raw = {str(channel_id): list(turns) for channel_id, turns in history.items()}
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")


def load_shared_turns(path=HISTORY_FILE):
    """Convenience for a caller (the terminal) that just wants the one shared conversation's
    turns as a plain list, not the full per-channel dict."""
    return list(load_history(path)[SHARED_KEY])


def append_shared_turn(user_text, reply_text, path=HISTORY_FILE):
    """Appends one real exchange to the shared conversation and persists it immediately, so
    the other interface picks it up on its very next read -- never held only in memory."""
    history = load_history(path)
    history[SHARED_KEY].append({"role": "user", "content": user_text})
    history[SHARED_KEY].append({"role": "assistant", "content": reply_text})
    save_history(history, path)
