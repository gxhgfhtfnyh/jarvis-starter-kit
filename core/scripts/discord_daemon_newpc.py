# -*- coding: utf-8 -*-
"""Companion to discord_daemon.py, for the second Jarvis bot identity that
represents the NEW PC's own session (built 2026-08-25, right after the owner
asked "if i hooked up another discord app to the session on this pc... both
sessions can talk to each other and talk to me"). Only ever runs on the new
PC -- discord_daemon.py (the original bot, DM-focused) keeps running
unchanged on `Nite`.

Deliberately simpler than discord_daemon.py: no DM handling (the owner DMs
the original bot, not this one), no photo/voice/status branches yet -- just
the shared-channel loop + a plain-text/reply outbox, since that's the
actual ask. Extend it the same way discord_daemon.py grew, if a real need
shows up.

Uses a SEPARATE keyring service ("discord_newpc") and separate log
files/outbox/singleton port from the original daemon, so nothing collides
even though most of this machinery is a close mirror.

Loop prevention against talking to `Nite`'s bot forever: this daemon logs
every message in the monitored channel(s) faithfully, INCLUDING ones from
the sibling bot on `Nite` -- but replying to one is a session decision, not
something this file enforces. The rule: only generate a reply to a message
authored by the sibling bot if it actually @-mentions this bot; never treat
every one of its messages as something to respond to.

Usage: pythonw.exe discord_daemon_newpc.py
"""
import asyncio
import json
import socket
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")
from jarvis_keyring import get_password as keyring_get_password
import discord

SERVICE = "discord_newpc"
LOG_DIR = Path(r"J:\Jarvis2\logs")
INCOMING_LOG = LOG_DIR / "discord_newpc_incoming.jsonl"
SENT_LOG = LOG_DIR / "discord_newpc_sent.jsonl"
OUTBOX_DIR = LOG_DIR / "discord_newpc_outbox"
DAEMON_LOG = LOG_DIR / "discord_newpc_daemon.log"
CHANNEL_CONFIG = Path(r"J:\Jarvis2\scripts\discord_channel_config.json")
OUTBOX_POLL_SEC = 0.25
SINGLE_INSTANCE_PORT = 50745  # one above discord_daemon.py's 50744


def _get_monitored_channel_ids():
    try:
        data = json.loads(CHANNEL_CONFIG.read_text(encoding="utf-8"))
        ids = data.get("monitored_channel_ids")
        return {int(c) for c in ids} if ids else set()
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return set()


def _acquire_singleton_lock():
    global _singleton_socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", SINGLE_INSTANCE_PORT))
    except OSError:
        _log("another discord_daemon_newpc.py instance already holds the lock - exiting")
        sys.exit(0)
    _singleton_socket = s
    return s


def _log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(DAEMON_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class JarvisNewPCDiscordClient(discord.Client):
    async def setup_hook(self):
        self.loop.create_task(self._handle_outbox())

    async def _handle_outbox(self):
        """Same outbox-poll pattern as discord_daemon.py: {"text": str,
        "channel_id": optional int (defaults to the first monitored
        channel), "reply_to": optional int}. No photo/voice/status support
        yet -- add it here the same way discord_daemon.py did, if needed."""
        await self.wait_until_ready()
        OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                files = sorted(OUTBOX_DIR.glob("*.json"))
                for fp in files:
                    try:
                        data = json.loads(fp.read_text(encoding="utf-8"))
                        text = data.get("text", "")
                        reply_to = data.get("reply_to")
                        channel_id = data.get("channel_id")
                    except (json.JSONDecodeError, OSError):
                        fp.unlink(missing_ok=True)
                        continue
                    if channel_id is None:
                        monitored = _get_monitored_channel_ids()
                        channel_id = next(iter(monitored), None)
                    if channel_id is None:
                        _log("SEND FAILED, no channel_id given and no monitored channel configured")
                        fp.unlink(missing_ok=True)
                        continue
                    send_channel = self.get_channel(int(channel_id))
                    if send_channel is None:
                        try:
                            send_channel = await self.fetch_channel(int(channel_id))
                        except Exception as e:
                            _log(f"SEND FAILED, can't resolve channel {channel_id}: {e}")
                            fp.unlink(missing_ok=True)
                            continue
                    try:
                        if reply_to:
                            ref_msg = await send_channel.fetch_message(int(reply_to))
                            sent = await send_channel.send(text, reference=ref_msg)
                        else:
                            sent = await send_channel.send(text)
                        _append_jsonl(SENT_LOG, {
                            "id": sent.id, "date": sent.created_at.isoformat(),
                            "text": text, "channel_id": channel_id, "reply_to": reply_to,
                        })
                        _log(f"sent to channel {channel_id}: {text[:80]!r}")
                    except Exception as e:
                        _log(f"SEND FAILED: {e}")
                    fp.unlink(missing_ok=True)
            except Exception:
                _log("outbox handler error:\n" + traceback.format_exc())
            await asyncio.sleep(OUTBOX_POLL_SEC)

    async def on_ready(self):
        _log(f"daemon started, logged in as {self.user} (id={self.user.id})")

    async def on_message(self, message):
        if message.author.id == self.user.id:
            return  # never react to this bot's own sends
        monitored = _get_monitored_channel_ids()
        if message.channel.id not in monitored:
            return  # not a configured channel -- ignore every other channel/server outright
        # Deliberately logs messages from any author including other bots
        # (e.g. Nite's Jarvis bot) -- unlike discord_daemon.py's default
        # stance, cross-session visibility is the whole point here. Loop
        # prevention (only reply if actually @-mentioned) is a session
        # decision made from this log, not enforced in this handler.
        try:
            reply_to = message.reference.message_id if message.reference else None
            mentions_me = self.user in message.mentions
            _append_jsonl(INCOMING_LOG, {
                "id": message.id,
                "date": message.created_at.isoformat(),
                "text": message.content,
                "reply_to": reply_to,
                "author_id": message.author.id,
                "author_name": message.author.display_name,
                "author_is_bot": message.author.bot,
                "mentions_me": mentions_me,
                "channel_id": message.channel.id,
            })
        except Exception:
            _log("incoming handler error:\n" + traceback.format_exc())


async def _main_with_reconnect():
    token = keyring_get_password(SERVICE, "bot_token")
    if not token:
        _log(f"no bot token in Credential Manager (service={SERVICE}, username=bot_token) - exiting")
        return
    intents = discord.Intents.default()
    intents.message_content = True

    backoff = 5
    while True:
        started_at = time.time()
        client = JarvisNewPCDiscordClient(intents=intents)
        try:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            await client.start(token)
            _log("client loop exited cleanly - reconnecting")
        except Exception:
            _log("client loop crashed, will retry:\n" + traceback.format_exc())
        finally:
            if not client.is_closed():
                await client.close()
        if time.time() - started_at > 60:
            backoff = 5
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _acquire_singleton_lock()
    try:
        asyncio.run(_main_with_reconnect())
    except Exception:
        _log("daemon crashed:\n" + traceback.format_exc())
        raise
