# -*- coding: utf-8 -*-
"""Milestone 5 proper (op a_4e656055, Nov 13 2026): real agent-to-agent coordination, not just
orchestrator-scripted sequencing. Up to now every "specialist talking to another specialist"
in Kyogen has actually been me calling role A, then deciding to call role B next -- a fixed
pipeline, not agents choosing to communicate. This module is the real gap-closer: a role can
POST a help request on its own initiative (triggered by its own low self-confidence, not by me
deciding it needs help), and another role's mailbox holds that request until something -- the
daemon's idle pass, or the requester itself next cycle -- picks it up and replies. The request
and the reply are both genuine role-authored content, not orchestrator glue text.

Trust boundary: same as swarm_daemon.py -- local-only JSON files under MAILBOX_DIR, not a
network-exposed queue. A mailbox message is data a role reads and reasons over, same trust
level as the rest of the pipeline's local files.
"""
import itertools
import json
import os
import time
import uuid

MAILBOX_DIR = r"C:\LocalAI\swarm_memory\mailbox"
_SEQ = itertools.count()


def _role_dir(role, mailbox_dir=MAILBOX_DIR):
    return os.path.join(mailbox_dir, role)


def post_message(from_role, to_role, content, kind="help_request", in_reply_to=None, mailbox_dir=MAILBOX_DIR):
    """Writes one message file into to_role's mailbox. Returns the message dict (includes its
    id, so a reply can set in_reply_to=that id to thread the conversation)."""
    msg = {
        "id": uuid.uuid4().hex[:12],
        "from": from_role,
        "to": to_role,
        "kind": kind,
        "content": content,
        "in_reply_to": in_reply_to,
        "ts": time.time(),
        # time.time() has coarse (~15ms on Windows) resolution, so two messages posted in quick
        # succession can land on the same ts -- this process-wide counter is the real tiebreaker
        # for ordering, not the random uuid filename.
        "seq": next(_SEQ),
    }
    d = _role_dir(to_role, mailbox_dir)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "%s.json" % msg["id"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(msg, f, indent=2)
    return msg


def get_messages(role, mailbox_dir=MAILBOX_DIR):
    """Returns unread messages waiting in role's mailbox, oldest first. Pure-ish (one directory
    listing + reads)."""
    d = _role_dir(role, mailbox_dir)
    if not os.path.isdir(d):
        return []
    files = sorted(f for f in os.listdir(d) if f.endswith(".json"))
    out = []
    for f in files:
        with open(os.path.join(d, f), encoding="utf-8") as fh:
            out.append(json.load(fh))
    return sorted(out, key=lambda m: (m["ts"], m.get("seq", 0)))


def consume_message(role, msg_id, mailbox_dir=MAILBOX_DIR):
    """Deletes the message so it is never re-read. Real delivery semantics: at-most-once, not
    at-least-once -- a role that crashes mid-handling loses the message rather than looping on
    it forever, acceptable for this local-only, low-volume use."""
    path = os.path.join(_role_dir(role, mailbox_dir), "%s.json" % msg_id)
    if os.path.exists(path):
        os.remove(path)
        return True
    return False


def request_help(from_role, to_role, problem_summary, mailbox_dir=MAILBOX_DIR):
    """The role-initiated trigger: a specialist that is stuck posts its own description of the
    problem (not a message I write for it) to a peer's mailbox. Returns the posted message."""
    return post_message(from_role, to_role, problem_summary, kind="help_request", mailbox_dir=mailbox_dir)


def reply_to_request(responder_role, original_msg, response_content, mailbox_dir=MAILBOX_DIR):
    """Posts the responder's reply back into the original requester's mailbox, threaded via
    in_reply_to, then consumes the original request so it is not answered twice."""
    reply = post_message(
        responder_role, original_msg["from"], response_content,
        kind="help_reply", in_reply_to=original_msg["id"], mailbox_dir=mailbox_dir,
    )
    consume_message(original_msg["to"], original_msg["id"], mailbox_dir)
    return reply
