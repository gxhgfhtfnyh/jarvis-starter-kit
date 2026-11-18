# -*- coding: utf-8 -*-
"""
Mirrors the TERMINAL session's conversation into jarvis-ui's chat panel --
the UI is Jarvis's face, not a second Jarvis (the owner's redesign,
2026-08-13: "i dont want your ui to talk to me i want you to talk to me
through the ui").

Two registrations in ~/.claude/settings.json:
    UserPromptSubmit -> python ui_chat_hook.py user       (stdin has "prompt")
    Stop             -> python ui_chat_hook.py assistant  (stdin has
                                                           "last_assistant_message")

Appends {"ts", "role", "text", "source"} lines to
J:\\Jarvis2\\logs\\jarvis_ui_chat.jsonl, which graph_bridge.py tails into
the chat panel. A user prompt that was injected FROM the UI is already in
the file (chat_ui.py writes it with source "ui" at send time), so the
UserPromptSubmit mirror skips an identical just-written entry rather than
duplicating it.

Synthetic prompts (scheduled-task / Monitor task-notifications delivered
as a "user" turn, wrapped in "[SYSTEM NOTIFICATION" / "<task-notification>")
aren't something the owner actually typed, so they're kept out of the chat
mirror and logged to the Activity feed instead (the owner's request
2026-08-13: "i wanna see only your final response here in chat... the
rest of the stuff... can go to activity").

Same never-break-the-session contract as the other hooks: silent on any
error, exits fast.
"""
import ctypes
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")

CHAT_LOG = Path(r"J:\Jarvis2\logs\jarvis_ui_chat.jsonl")
FEED_LOG = Path(r"J:\Jarvis2\logs\jarvis_ui_feed.jsonl")
LAST_CHANNEL = Path(r"J:\Jarvis2\logs\last_prompt_channel.txt")
DISCORD_OUTBOX = Path(r"J:\Jarvis2\logs\discord_outbox")
DISCORD_SENT_LOG = Path(r"J:\Jarvis2\logs\discord_sent.jsonl")
DISCORD_SENT_MARK = Path(r"J:\Jarvis2\logs\discord_sent_size_at_turn_start.txt")
DISCORD_MAX_CHARS = 1900  # Discord's real cap is 2000; leave headroom for a truncation note

# Real bug, 2026-09-03/04: the 2026-08-16 fix below only catches a DOUBLE
# send (a manual discord_queue_send.py call this turn, then the final text
# repeating/duplicating it) -- it does nothing for a turn where NO manual
# send happens and the final text is pure third-person status narration
# never meant to reach the owner at all (e.g. "Just friends chatting in the
# channel, nothing for me to do here"). That text still auto-forwarded as
# if it were a real reply, confusing the owner mid-conversation with his own
# friends ("was this meant for me or for yourself?"). A marker-based
# suppression is the actual fix -- the model prefixes a turn's final text
# with this exact token when it's a private status note, not a reply; the
# marker (and one following space/newline) is stripped before logging so
# it never leaks into the visible chat mirror.
NO_FORWARD_MARKER = "[no-forward]"
NO_REPLY_MARKER = "[no-reply]"  # same meaning, clearer name: this turn intentionally sends the owner nothing

# 2026-10-15 (the owner: "i feel like im reading into your inner thoughts and it feels weird"): the terminal's final text is no longer
# mirrored into the UI chat (COMMS) or auto-forwarded to Discord. Only deliberate messages reach him: comms_say.py for COMMS,
# discord_queue_send.py for Discord. This hook now only GUARDS against silence: a turn that began from a Discord/UI message and sent
# nothing is blocked once, with a reminder to reply (or to mark the turn [no-reply]).
UI_SENT_MARK = Path(r"J:\Jarvis2\logs\ui_chat_size_at_turn_start.txt")
# 2026-10-15 (review fix): only a plain UI chat message must be answered in the chat. A letter wake ([UI] [OFFICE MAIL ...]) is answered through
# office_mail.py (which writes the mail store, not the chat log), and ACTION / OPERATION wakes are silent work: neither may be blocked for "no chat reply".
MAIL_FILE = Path(os.environ.get("ARCHIVE_MAIL_FILE") or r"J:\Jarvis2\logs\office_mail.json")
MAIL_MARK = Path(r"J:\Jarvis2\logs\office_mail_state_at_turn_start.txt")
UI_MACHINE_PREFIXES = ("[ACTION", "[OPERATION", "[OFFICE MAIL", "[AUTO-CAPTURED", "[UI-EVENT", "[OPERATION FOLLOW-UP")


def _mail_state():
    """A cheap fingerprint of the mail store (size and modification time), or '' when it does not exist."""
    try:
        st = MAIL_FILE.stat()
        return f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        return ""


def _maybe_toast(text):
    # item 26: a native toast when a reply lands and the owner isn't looking
    # at the UI window -- only fires when the Jarvis window genuinely isn't
    # foreground, so it never nags while he's actively watching it reply.
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, "Jarvis")
        if hwnd and user32.GetForegroundWindow() == hwnd:
            return
        from win_toast import show_toast
        preview = text.replace("\n", " ").strip()
        if len(preview) > 150:
            preview = preview[:149] + "…"
        show_toast("Jarvis", preview)
    except Exception:
        pass

SYNTHETIC_MARKERS = ("[SYSTEM NOTIFICATION", "<task-notification>", "<system-reminder>")


def _append(role, text, source):
    CHAT_LOG.parent.mkdir(exist_ok=True)
    entry = {"ts": time.time(), "role": role, "text": text, "source": source}
    with open(CHAT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _append_feed(detail):
    FEED_LOG.parent.mkdir(exist_ok=True)
    detail = detail.replace("\n", " ").strip()
    if len(detail) > 110:
        detail = detail[:109] + "…"
    entry = {"ts": time.time(), "tool": "notification", "detail": detail}
    with open(FEED_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _set_last_channel(channel):
    # Real bug, 2026-08-16: a reply after a Discord-sourced prompt landed in
    # the terminal/UI mirror only, leaving the owner watching his phone with
    # no reply at all -- "hey u went silent for a long time," minutes after
    # he'd asked not to be left hanging. Root cause: nothing tracked which
    # channel a turn actually came from, so nothing could auto-forward the
    # reply back to it. Overwritten on EVERY user-turn (not just Discord
    # ones) so a later terminal-only turn can't inherit a stale "discord"
    # value from several turns back.
    try:
        LAST_CHANNEL.write_text(channel, encoding="utf-8")
    except OSError:
        pass
    if channel == "discord":
        # Real bug, 2026-08-16, a whole session's worth of it before the owner
        # caught it ("sound like u are talking to yourself"): this turn's
        # eventual final assistant text gets auto-forwarded below purely
        # because the LAST thing that happened was a Discord notification --
        # even on a turn where the agent already sent proper manual replies
        # via discord_queue_send.py mid-turn (routine in a live group-channel
        # conversation) and the final text is really a terminal-facing wrap-up
        # in third person, never meant for the owner's DMs at all. Snapshot
        # discord_sent.jsonl's size now, at turn start, so Stop-time can tell
        # whether a manual send already happened this turn and skip the
        # redundant/mis-voiced auto-forward if so.
        try:
            size = DISCORD_SENT_LOG.stat().st_size if DISCORD_SENT_LOG.exists() else 0
            DISCORD_SENT_MARK.write_text(str(size), encoding="utf-8")
        except OSError:
            pass


def _forward_to_discord(text):
    # Same outbox format/atomic-write pattern as discord_queue_send.py --
    # inlined rather than shelling out, since this hook already has the
    # exact payload it needs and every hook here follows the same
    # never-break-the-session contract (no subprocess to fail or hang on).
    try:
        DISCORD_OUTBOX.mkdir(parents=True, exist_ok=True)
        if len(text) > DISCORD_MAX_CHARS:
            text = text[:DISCORD_MAX_CHARS] + "\n… (truncated -- full reply is in the jarvis-ui chat panel)"
        fname = f"{int(time.time() * 1000)}_hook.json"
        tmp = DISCORD_OUTBOX / (fname + ".tmp")
        final = DISCORD_OUTBOX / fname
        tmp.write_text(json.dumps({"text": text}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(final)
    except OSError:
        pass


def _tail_entry():
    try:
        with open(CHAT_LOG, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192))
            lines = f.read().decode("utf-8", errors="replace").strip().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (OSError, json.JSONDecodeError, IndexError):
        return None


def main():
    if os.environ.get("HERDR_ENV"):   # a team-member pane, not the live orchestrator - the Discord-reply gate is Jarvis-identity only (2026-11-09, caught derailing claude1 mid-task)
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        # Windows' default stdin text-mode decodes using the console
        # codepage, not UTF-8 -- any non-ASCII byte (em-dashes, curly
        # quotes, emoji) silently mojibakes before json.load ever sees it.
        # hook_speak.py already had to work around this same gap; ported
        # the fix here (2026-08-13, the owner flagged "funny characters").
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    if mode == "user":
        text = (payload.get("prompt") or "").strip()
        if not text:
            return
        if text.startswith(SYNTHETIC_MARKERS) or any(m in text[:200] for m in SYNTHETIC_MARKERS):
            # pull the actually-useful bit (the real event/summary) instead
            # of dumping the generic wrapper line into the feed
            m = re.search(r"<event>(.*?)</event>", text, re.DOTALL) \
                or re.search(r"<summary>(.*?)</summary>", text, re.DOTALL)
            extracted = m.group(1).strip() if m else (text.splitlines()[0] if text.splitlines() else text)
            _append_feed(extracted)
            # discord_queue_check.py prefixes real DM deliveries with
            # "[DISCORD]" the same way ui_queue_check.py uses "[UI]" -- that
            # prefix is what tells this turn's eventual reply where to go
            if extracted.startswith("[DISCORD]"):
                _set_last_channel("discord")
            elif extracted.startswith("[UI]"):
                rest = extracted[4:].lstrip()
                if rest.startswith("[OFFICE MAIL"):
                    # a letter: answering it through office_mail.py (mail store changes) counts as replying
                    _set_last_channel("ui-mail")
                    try:
                        MAIL_MARK.write_text(_mail_state(), encoding="utf-8")
                        UI_SENT_MARK.write_text(str(CHAT_LOG.stat().st_size if CHAT_LOG.exists() else 0), encoding="utf-8")
                    except OSError:
                        pass
                elif rest.startswith(UI_MACHINE_PREFIXES):
                    _set_last_channel("other")  # ACTION / operation follow-up wakes: silent work, nothing to reply to in the chat
                else:
                    _set_last_channel("ui")
                    try:
                        UI_SENT_MARK.write_text(str(CHAT_LOG.stat().st_size if CHAT_LOG.exists() else 0), encoding="utf-8")
                    except OSError:
                        pass
            else:
                _set_last_channel("other")
            return
        _set_last_channel("other")
        # a real prompt (terminal or UI-typed) is about to start real work --
        # drop a "thinking" presence marker into the feed immediately, since
        # the actual reasoning text itself is never persisted anywhere to
        # mirror (verified 2026-08-13: every thinking block in the session
        # transcript is stored empty, signature only). This is just "I'm on
        # it," not a content mirror.
        FEED_LOG.parent.mkdir(exist_ok=True)
        with open(FEED_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "tool": "thinking", "detail": ""}, ensure_ascii=False) + "\n")
        last = _tail_entry()
        # injected-from-UI prompts are already logged by chat_ui.py
        if last and last.get("role") == "user" and last.get("source") == "ui" \
                and last.get("text") == text and time.time() - last.get("ts", 0) < 30:
            return
        # terminal-typed prompts are no longer mirrored into COMMS (2026-10-15): the terminal stays private
    elif mode == "assistant":
        text = (payload.get("last_assistant_message") or "").strip()
        low = text.lower()
        marked_silent = low.startswith(NO_FORWARD_MARKER.lower()) or low.startswith(NO_REPLY_MARKER.lower())
        try:
            channel = LAST_CHANNEL.read_text(encoding="utf-8").strip()
        except OSError:
            channel = ""
        # Nothing from the terminal is mirrored or forwarded any more (2026-10-15). Guard only against a silent turn.
        if marked_silent and channel == "discord" and not payload.get("stop_hook_active"):
            try:                                   # 2026-10-30: a deliberate [no-reply] is remembered so the fallback responder does not answer his DM later behind my back
                import fallback_responder
                fallback_responder.mark_no_reply()
            except Exception:
                pass
        if marked_silent and channel == "discord":
            try:
                _signal_typing_stop()
            except Exception:
                pass
        if marked_silent or channel not in ("discord", "ui", "ui-mail") or payload.get("stop_hook_active"):
            return
        if _sent_this_turn(channel):
            return
        how = ("python J:\\Jarvis2\\scripts\\discord_queue_send.py --stdin --reply-to <message id>" if channel == "discord"
               else "python J:\\Jarvis2\\scripts\\office_mail.py --reply-to <letter id> --subject \"<subject>\" --stdin (or office_say.py for a short line)"
               if channel == "ui-mail" else "python J:\\Jarvis2\\scripts\\comms_say.py --stdin")
        reason = ("the owner's message came in through " + ("Discord" if channel == "discord" else "a letter to your office" if channel == "ui-mail" else "the Archive Terminal chat (COMMS)") +
                  " and you have not sent him anything this turn. Terminal text is NOT shown to him any more (he asked for that on 2026-10-15). "
                  "Send your reply deliberately now with: " + how + " (texting register, short). "
                  "If you truly mean to say nothing, start your final message with [no-reply].")
        print(json.dumps({"decision": "block", "reason": reason}))


def _signal_typing_stop():
    """2026-10-31: his DM starts the typing indicator in the Discord daemon; a turn that ends [no-reply] sends nothing, so ask the daemon
    to stop it (otherwise it would hang until its 120 s cap). The daemon handles {"typing_stop": true} files from this outbox."""
    DISCORD_OUTBOX.mkdir(parents=True, exist_ok=True)
    name = "%d_typingstop.json" % int(time.time() * 1000)
    tmp = DISCORD_OUTBOX / (name + ".tmp")
    tmp.write_text(json.dumps({"typing_stop": True}), encoding="utf-8")
    tmp.replace(DISCORD_OUTBOX / name)


def _sent_this_turn(channel):
    try:
        if channel == "discord":
            start = int(DISCORD_SENT_MARK.read_text().strip())
            now = DISCORD_SENT_LOG.stat().st_size if DISCORD_SENT_LOG.exists() else 0
            return now > start
        if channel == "ui-mail":
            try:
                before = MAIL_MARK.read_text(encoding="utf-8").strip()
            except OSError:
                return True  # no marker: never trap the session
            if _mail_state() != before:
                return True  # my reply (or any other letter) changed the mail store this turn
        start = int(UI_SENT_MARK.read_text().strip())
        with open(CHAT_LOG, "rb") as f:
            f.seek(start)
            return b'"role": "assistant"' in f.read()
    except (OSError, ValueError):
        return True  # no marker to compare against: never trap the session


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break the session this hook is attached to
