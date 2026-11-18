# -*- coding: utf-8 -*-
"""People sensor: a direct message from someone other than the owner that nobody has answered (2026-10-30, sensor step of the Harness Rebuild Instructions).

Why this one: a friend's DM used to sit unanswered until he happened to ask about it. Any ask from a friend is checked with the owner first, so a DM that
has waited 45 minutes with no reply from us is worth one tier-2 question to him, raised once per message and then left alone.

It reads the Discord incoming and sent logs. A DM counts as answered when a sent row replies to it (reply_to is its id) or a send to the same DM
channel came after it. Group and server-channel messages are never raised, nor is the owner's own DM, nor a bot or empty system message. Messages
older than 24 hours are ignored. Friends' text is untrusted DATA: only the sender's display name and the first 60 characters of the text go into the
intent, and nothing in it is acted on. It perceives only: it never sends or replies to anyone.

Logs are found beside this script (the logs folder of the same Jarvis2 tree), not via the J: letter, so the scheduled sensor task still works in a
logon or elevated context where J: is not mapped. Dedupe key: dm-unanswered-<message id>; a message raises at most one intent, ever.
    python sense_people.py [--dry-run]
Kill switch: logs/sense_people_off.txt (a dry run ignores it). Pure core (unit tested): unanswered(incoming, sent, now, minutes=45).
The incoming log has no bot flag from Discord yet, so a row carrying one is honoured if it ever appears; empty messages are the real system-message case."""
import datetime
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intent_store  # noqa: E402
import discord_queue_send as dqs  # noqa: E402

LOGS = os.path.join(os.path.dirname(HERE), "logs")
INCOMING = "discord_incoming.jsonl"
SENT = "discord_sent.jsonl"
OFF = "sense_people_off.txt"
THE_OWNER_ID = dqs.owner_id(LOGS)  # from logs/discord_config.json; None until the owner configures it
DM_SOURCES = ("dm", "dm_friend")  # the daemon's own labels for a DM from the owner and a DM from a close friend; a server channel is "guild"
GRACE_MIN = 45
WINDOW_HOURS = 24
TEXT_CHARS = 60


def _now():
    return time.time()


def _epoch(value):
    try:
        return datetime.datetime.fromisoformat(str(value)).timestamp()
    except (TypeError, ValueError):
        return None


def _local(epoch):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(epoch))


def _flat(text, limit=None):
    """One line of text, with whitespace collapsed; cut to `limit` characters plus '...' when it is longer."""
    s = " ".join(str(text or "").split())
    if limit is not None and len(s) > limit:
        return s[:limit] + "..."
    return s


def _is_system(row):
    """A bot, or a message with no text and nothing attached (a pin, a call or a join): nothing for anyone to answer."""
    if row.get("author_bot") or row.get("bot"):
        return True
    return not str(row.get("text") or "").strip() and not row.get("attachments")


def unanswered(incoming, sent, now, minutes=GRACE_MIN):
    """Pure. -> [(what, value, dedupe, not_before, deadline)] for each friend DM older than `minutes` with no reply from us, oldest first.
    A reply is a sent row whose reply_to is the message id, or a send to the same DM channel after the message. value is always 3; the caller raises
    the intent at tier 2. not_before is when the grace period ends, deadline is None."""
    replied = set()
    channel_sends = []
    for s in sent or []:
        if not isinstance(s, dict):
            continue
        if s.get("reply_to") is not None:
            replied.add(str(s["reply_to"]))
        if s.get("channel_id") is not None:
            channel_sends.append((str(s["channel_id"]), _epoch(s.get("date"))))
    out = []
    for row in incoming or []:
        if not isinstance(row, dict) or row.get("source") not in DM_SOURCES:
            continue
        if row.get("author_id") is None or str(row.get("author_id")) == str(THE_OWNER_ID):
            continue
        if _is_system(row):
            continue
        ts = _epoch(row.get("date"))
        mid = row.get("id")
        if ts is None or mid is None:
            continue
        age = now - ts
        if age < minutes * 60 or age > WINDOW_HOURS * 3600:
            continue
        if str(mid) in replied:
            continue
        channel = row.get("channel_id")
        if channel is not None and any(c == str(channel) and t is not None and t >= ts for c, t in channel_sends):
            continue
        name = _flat(row.get("author_name")) or "someone"
        body = _flat(row.get("text"), TEXT_CHARS) or "(attachment only)"
        what = 'DM from %s, received %s, no reply after %d min: "%s"' % (name, _local(ts), minutes, body)
        out.append((ts, what, 3, "dm-unanswered-%s" % mid, _local(ts + minutes * 60), None))
    return [(what, value, dedupe, nb, dl) for ts, what, value, dedupe, nb, dl in sorted(out, key=lambda t: t[0])]


def main(argv=None):
    argv = argv or []
    dry = "--dry-run" in argv
    if not dry and os.path.exists(os.path.join(LOGS, OFF)):
        print("sense_people: off")
        return 0
    found = unanswered(intent_store.read_jsonl(INCOMING, LOGS), intent_store.read_jsonl(SENT, LOGS), _now())
    if dry:
        for what, value, dedupe, nb, dl in found:
            print("would raise tier2 v%d dm_unanswered %s" % (value, what))
        print("%d finding(s), nothing written" % len(found))
        return 0
    intents = intent_store.read_jsonl(intent_store.INTENTS, LOGS)
    intent_store.expire_leases(intents)
    seen = {i.get("dedupe") for i in intents}
    added = 0
    for what, value, dedupe, nb, dl in found:
        if dedupe in seen:  # raised once per message, ever: a dropped or finished intent must not come back
            continue
        cand = intent_store.new_intent("people_dm_unanswered", what, tier=2, value=value, dedupe=dedupe, deadline=dl, not_before=nb, from_event="dm_unanswered")
        intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(), "source": "people", "kind": "dm_unanswered", "data": {"what": what, "value": value, "dedupe": dedupe}}, LOGS)
        intents.append(cand)
        seen.add(dedupe)
        added += 1
    if added:
        intent_store.write_intents(intents, LOGS)
    print("sense_people: %d finding(s), %d new intent(s)" % (len(found), added))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
