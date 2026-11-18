# -*- coding: utf-8 -*-
"""Link sensor: notices a link the owner sent that nobody has looked at yet (2026-10-30, second sensor of the redesigned harness).

Why this one: mining his last 3 days of messages found "research this" or "study this link" was his most frequent ask (51 of 561 messages), and
measuring the DM log found 31 links in 4 days, 20 of them YouTube. Today he sent eleven videos and had to chase me for progress. A sensor that notices
an unread link the moment it arrives turns the most common thing he asks for into something already under way.

It reads the Discord incoming log, keeps the ids it has already raised in logs/sense_links_state.json, and raises ONE intent per unprocessed link
(tier 1: read it and tell him what it changes for us; tier 2 for a host that needs his account, since that is his call). It never opens a link itself:
perceive only. Kill switch: logs/sense_links_off.txt.
    python sense_links.py [--dry-run] [--since-hours 48]
Pure core (unit tested): links_in(text), classify(url), unprocessed(rows, seen, owner_id, cutoff)."""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402
LOGS = os.environ.get("HARNESS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
STATE = "sense_links_state.json"
OFF = "sense_links_off.txt"
OWNER = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it
URL = re.compile(r"https?://[^\s<>()\[\]]+", re.I)
# host fragment -> (what reading it means, tier); a host that needs his own signed-in account is his call, not mine
KINDS = (("youtu", ("video", 1)), ("youtube.com", ("video", 1)), ("reddit.com", ("reddit thread", 1)), ("github.com", ("repo or file", 1)),
         ("arxiv.org", ("paper", 1)), ("anthropic.com", ("docs page", 1)), ("claude.com", ("docs page", 1)),
         ("instagram.com", ("instagram post (needs his account)", 2)), ("facebook.com", ("facebook post (needs his account)", 2)),
         ("chatgpt.com", ("shared chatgpt chat (needs his account)", 2)), ("x.com", ("post (needs his account)", 2)), ("twitter.com", ("post (needs his account)", 2)))


def links_in(text):
    """Every http(s) link in a message, trailing punctuation stripped, in order and without repeats."""
    out = []
    for u in URL.findall(text or ""):
        u = u.rstrip(".,;:!?'\"")
        if u not in out:
            out.append(u)
    return out


def classify(url):
    """(what it is, tier) for a link; anything unknown is a page I may read and report on."""
    host = (url or "").split("//", 1)[-1].split("/", 1)[0].lower()
    for frag, kind in KINDS:
        if frag in host:
            return kind
    return ("page", 1)


def _epoch(date_str):
    try:
        return time.mktime(time.strptime((date_str or "")[:19], "%Y-%m-%dT%H:%M:%S")) - time.timezone
    except Exception:
        return 0.0


def unprocessed(rows, seen, owner_id=OWNER, cutoff=0.0):
    """Pure. -> [(url, message id, epoch)] for links he sent after cutoff whose url is not in `seen`, oldest first, no duplicate urls."""
    out, have = [], set(seen or [])
    for m in rows:
        if not isinstance(m, dict) or m.get("author_id") != owner_id or m.get("source") != "dm":
            continue
        ts = _epoch(m.get("date"))
        if ts < cutoff:
            continue
        for u in links_in(m.get("text", "")):
            if u in have:
                continue
            have.add(u)
            out.append((u, m.get("id"), ts))
    return sorted(out, key=lambda t: t[2])


def _load_state(logs=None):
    try:
        return json.load(open(os.path.join(logs or LOGS, STATE), encoding="utf-8"))
    except (OSError, ValueError):
        return {"seen": []}


def _save_state(state, logs=None):
    json.dump(state, open(os.path.join(logs or LOGS, STATE), "w", encoding="utf-8"))


def main(argv=None):
    argv = argv or []
    if os.path.exists(os.path.join(LOGS, OFF)):
        print("sense_links: off")
        return 0
    hours = int(argv[argv.index("--since-hours") + 1]) if "--since-hours" in argv else 48
    import intent_store
    rows = intent_store.read_jsonl("discord_incoming.jsonl")
    state = _load_state()
    found = unprocessed(rows, state.get("seen", []), cutoff=time.time() - hours * 3600)
    if "--dry-run" in argv:
        for u, mid, ts in found:
            what, tier = classify(u)
            print("would raise tier%d %-34s %s" % (tier, what, u[:70]))
        print("%d unread link(s) in the last %dh, nothing written" % (len(found), hours))
        return 0
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    intent_store.expire_leases(intents)
    added = 0
    for u, mid, ts in found:
        what, tier = classify(u)
        intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(ts), "source": "links", "kind": "unread_link", "data": {"url": u, "msg": mid, "what": what}})
        cand = intent_store.new_intent("unread_link", "read this %s he sent and tell him what it changes for us: %s" % (what, u), tier=tier, value=3,
                                       dedupe="link-" + u[-60:], from_event=str(mid))
        if intent_store.is_duplicate(intents, cand):
            continue
        intents.append(cand)
        added += 1
    if added:
        intent_store.write_intents(intents)
    state["seen"] = (state.get("seen", []) + [u for u, _m, _t in found])[-500:]
    _save_state(state)
    print("sense_links: %d unread link(s), %d new intent(s)" % (len(found), added))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
