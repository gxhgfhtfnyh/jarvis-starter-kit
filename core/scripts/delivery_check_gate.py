# -*- coding: utf-8 -*-
"""Delivery check gate (2026-11-12, the "results never sent" gap named in the Correction Ledger (10-29 14:44, cause
[no-delivery]: "saved the researcher's report to the vault but did not message him the findings") and reconfirmed by
log-mining tonight as a real recurring pattern, not a one-off.

claim_gate.py already catches the opposite mistake -- claiming a Discord send happened when it didn't. This catches
the quieter failure: a real vault write happens (a finding, a research note, a report) and nothing is ever actually
sent to him about it in the same turn -- no false claim, just silence. Terminal text is not shown to him any more
(his 2026-10-15 instruction, see jarvis-channels), so a vault write with no discord_queue_send/comms_say call and no
[no-reply] marker in the reply is a real gap, not just a quiet background step.

Stop hook: same shape as claim_gate.py and correction_ledger_gate.py. Never blocks by itself; raises a tier-1 intent
that keep_going.py's existing stop gate holds open until closed with real evidence.

Testable core: vault_write_in_turn(calls), delivery_call_in_turn(calls), is_no_reply(text), find_undelivered(rows)."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

VAULT_PATH = re.compile(r"Jarvis-Brain", re.I)
DELIVERY_CALL = re.compile(r"\bdiscord_queue_send\b|\bcomms_say\b", re.I)
NO_REPLY = re.compile(r"^\s*\[no-reply\]", re.I)

# 2026-11-14, found live: this file had its own copy of the "scan for the last user-role row"
# pattern with NO synthetic-row exclusion at all -- a <task-notification>/<system-reminder> row
# landing after a real [no-reply] reply (e.g. a Monitor-expiry notice arriving right after I
# finished a turn) resets `start` past my own real reply, so the delivery call never gets seen.
# correction_ledger_gate.py already solved this exact problem (and had to generalize it twice,
# see feedback_patch_not_root_cause_2026_11_12.md) -- reusing its patterns here instead of
# drifting a third, slightly-different copy of the same regex into a new file.
try:
    from correction_ledger_gate import SYNTHETIC_WRAPPER, SYNTHETIC_SKILL_BODY
except Exception:  # noqa: BLE001 -- never let a sibling-module import break this gate
    SYNTHETIC_WRAPPER = re.compile(r"^\s*<[A-Za-z][\w:-]*(?:\s[^<>]*)?/?>")
    SYNTHETIC_SKILL_BODY = re.compile(r"^\s*\(Re-invocation of /|^\s*Base directory for this skill:", re.I)


def _is_synthetic(text):
    """Pure. True when `text` is a harness-injected row (a tag-wrapped notification, or a Skill
    re-invocation body), never something the owner actually typed."""
    return bool(SYNTHETIC_WRAPPER.match(text) or SYNTHETIC_SKILL_BODY.match(text))


def vault_write_in_turn(calls):
    """Pure. True when any Write/Edit call in `calls` ([(name, input), ...]) targets a path under the vault."""
    for name, inp in calls or []:
        if name in ("Write", "Edit", "NotebookEdit"):
            path = (inp or {}).get("file_path", "")
            if VAULT_PATH.search(path):
                return True
    return False


def delivery_call_in_turn(calls):
    """Pure. True when any Bash/PowerShell command in `calls` already ran discord_queue_send or comms_say."""
    for name, inp in calls or []:
        if name in ("Bash", "bash", "PowerShell"):
            cmd = (inp or {}).get("command", "")
            if DELIVERY_CALL.search(cmd):
                return True
    return False


def is_no_reply(text):
    """Pure. True when the reply text opens with the established [no-reply] marker (see jarvis-channels skill)."""
    return bool(NO_REPLY.match((text or "").strip()))


def _blocks(row):
    c = (row.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def find_undelivered(rows):
    """Pure. Scan transcript rows for the last turn's tool calls and final reply text.
    -> True when a vault write happened, no delivery call happened, and the reply isn't a [no-reply]."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("type") in ("user", "assistant")]
    start = 0
    for i, r in enumerate(rows):
        if r["type"] != "user":
            continue
        content = (r.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in _blocks(r) if b.get("type") == "text")
        if text.strip() and not _is_synthetic(text):
            start = i
    calls, last_text = [], ""
    for r in rows[start:]:
        for b in _blocks(r):
            if r["type"] == "assistant" and b.get("type") == "tool_use":
                calls.append((b.get("name"), b.get("input") or {}))
            elif r["type"] == "assistant" and b.get("type") == "text":
                last_text = b.get("text", "")
    if not vault_write_in_turn(calls):
        return False
    if delivery_call_in_turn(calls):
        return False
    return not is_no_reply(last_text)


def _raise_undelivered_intent():
    import hashlib
    import time

    import intent_store

    dedupe = "undelivered-%s" % hashlib.sha1(time.strftime("%Y%m%d%H%M").encode()).hexdigest()[:8]
    what = (
        "UNDELIVERED: a vault write happened this turn with no discord_queue_send/comms_say call and no [no-reply] "
        "marker. Either send him the result now, or close this intent --result skipped with why it was genuinely "
        "background work he doesn't need pinged about."
    )
    cand = intent_store.new_intent("undelivered_result", what, tier=1, value=3, dedupe=dedupe, from_event="delivery_check")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(r"J:\Jarvis2\logs\delivery_check_gate_off.txt"):
            return
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace") if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        data = json.loads(raw or "{}")
        path = data.get("transcript_path")
        if not path:
            return
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-600:]
        rows = []
        for line in lines:
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                pass
        if find_undelivered(rows):
            _raise_undelivered_intent()
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
