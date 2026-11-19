# -*- coding: utf-8 -*-
"""Correction ledger gate (2026-11-12, built after the owner named the abandoned Correction Ledger as a real, live example
of "a real mechanism exists but nothing enforces it" -- the ledger's manual `ledger.py add` path depends on me remembering
to run it per correction, and the automatic path in learn_outcomes.py only fires for intent-KIND-level demotions (5+ closed,
under 50% acceptance), never for an individual one-off correction like most of tonight's. Same shape as claim_gate.py's
existing refusal-to-intent flow (_raise_gaps): detect the signal, raise a tier-1 intent, let keep_going.py hold the stop
open until it's actually closed with real evidence (a ledger.py add call, or --result skipped with a stated reason).

Stop hook: reads the hook JSON from stdin, finds the last user message in the turn, checks it for the existing
correction regex (shared with learn_outcomes.py/scoreboard.py so detection logic stays in one place), and -- if it
looks like a correction and this turn has no evidence a ledger.py add call already happened -- raises one capability_gap-
style intent per message id (deduped so the same message never raises twice). Never blocks by itself; keep_going.py's
existing stop gate is what actually holds the turn open on an unclosed intent.

Testable core: looks_like_correction(text), ledger_call_in_turn(calls), find_correction(rows) -> (msg_id, text) or None."""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

try:
    from scoreboard import CORRECTION_RX
except Exception:  # noqa: BLE001 -- keep working if scoreboard is unavailable, same fallback learn_outcomes.py uses
    CORRECTION_RX = r"mistake|wrong|forgot|assum|didnt implement|dropped|losing? focus|even after"

CORRECT = re.compile(
    CORRECTION_RX.replace(r"|\bagain\b", "")
    + r"|\bnot what i (asked|meant|wanted)|\bi (didn.?t|did not) (ask|want)|\bstop (doing|sending)\b|\bwhy (did|are) (u|you)\b",
    re.I,
)

LEDGER_CALL = re.compile(r"\bledger\.py\b.{0,10}\badd\b", re.I)

# 2026-11-12, found live: a tool/monitor notification (<task-notification>, <system-reminder>) or a post-compaction
# summary (<artifact-content-authored-by-others/>) can each arrive as their own synthetic "user" transcript row,
# ranking as the "last user message" even though the owner never typed any of it -- a compaction summary's huge recap
# text matched CORRECT on a stray word ("wrong"/"assum"/etc.) the second time this fired. Fixing the second instance
# by naming that one tag too would just leave a third sibling tag as the next live false-positive (named in
# feedback_patch_not_root_cause_2026_11_12.md). Root cause instead: his real messages never open with an
# angle-bracket tag at all, synthetic or not, so match the general shape (an opening/self-closing tag as the very
# first thing in the row) rather than an enumerated tag-name whitelist.
SYNTHETIC_WRAPPER = re.compile(r"^\s*<[A-Za-z][\w:-]*(?:\s[^<>]*)?/?>")

# 2026-11-14, found live: a Skill-tool re-invocation injects the skill's own body text (e.g. the
# research skill's SKILL.md plus whatever ARGUMENTS were passed) as a plain "user" row -- no
# angle-bracket tag at all, so SYNTHETIC_WRAPPER above missed it, and a stray trigger word
# somewhere inside the skill's own markdown got read as a live correction from him. The one
# structural fingerprint common to every Skill re-invocation, regardless of which skill, is the
# harness's own "Base directory for this skill:" preamble line -- matching THAT shape (not
# "research" or any other skill name) keeps this a general fix, not a third enumerated instance
# of the same patch-not-root-cause trap named in feedback_patch_not_root_cause_2026_11_12.md.
#
# 2026-11-15, THIRD instance, same trap: invoking update-config injected its SKILL.md starting
# directly with "# Update Config Skill" (a markdown H1, no "Base directory" preamble line at all)
# -- still missed. Enumerating yet another specific preamble phrase would just leave a fourth
# skill with a different opening line as the next false positive. Real structural fingerprint
# this time: his actual texted messages (see the VOICE hook: short, lowercase, no lists) NEVER
# open with a markdown ATX header, while every skill body does (its own "# <Skill Name>" title or
# YAML frontmatter). Match that shape generally instead of any skill-specific preamble text.
SYNTHETIC_SKILL_BODY = re.compile(
    r"^\s*\(Re-invocation of /|^\s*Base directory for this skill:|^\s*#{1,6}\s|^\s*---\s*$", re.I
)

# 2026-11-19, FOURTH instance of the same patch-not-root-cause trap, and the biggest one yet: every correction
# the owner sent tonight came in over Discord (the whole session is Discord-relay driven), and NOT ONE of them ever
# raised a correction_unlogged intent -- he had to call it out live ("why are u putting it in backlog... thats a
# bad habit") before a fix happened at all. Root cause, confirmed by reading the raw transcript lines directly:
# a Discord-relayed message never lands as a plain {"type": "user", "message": {"content": "<his words>"}} row in
# this harness's JSONL at all. inbox_hook.py's PostToolUse context (the one authoritative, structurally-guaranteed
# carrier of his real words -- built only from `author_id == OWNER and source == "dm"`) lands on rows typed
# "attachment" / "api-request-blob" / "queue-operation", none of which `find_correction`'s own type filter
# (`r.get("type") in ("user", "assistant")`) ever kept. So the whole SYNTHETIC_WRAPPER/SYNTHETIC_SKILL_BODY
# filtering chain above never even got a chance to see his real text -- the rows carrying it were discarded one
# step earlier. Fix: don't try to guess which row "type" is real this time (that's the same enumerate-and-miss
# shape as the first three instances); instead key off inbox_hook's own unambiguous "id <N>: <text>" line, which
# only ever gets written from a confirmed real DM, and scan it out of EVERY row regardless of type.
DISCORD_DM_HEADER = "NEW DISCORD DM from the owner"
DISCORD_DM_LINE = re.compile(r"^id (\d+): (.+)$", re.M)


def _iter_strings(obj):
    """Pure. Yield every string leaf anywhere inside a nested dict/list structure. A hook's
    stdout is itself a JSON blob captured as a plain string (double-encoded: its own newlines
    are literal backslash-n, not real newline characters, until this re-parses it) -- recurse
    into any string that looks like JSON so DISCORD_DM_LINE's real-newline anchors still match."""
    if isinstance(obj, str):
        yield obj
        stripped = obj.strip()
        if stripped[:1] in ("{", "["):
            try:
                yield from _iter_strings(json.loads(stripped))
            except Exception:  # noqa: BLE001 -- not actually JSON, just a string that starts with { or [
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _iter_strings(v)


def find_last_discord_message(rows):
    """Pure. Scan every row (any type -- the bug above was trusting a type filter that misses them) for
    inbox_hook's own '[NEW DISCORD DM from the owner ...]' block and return (index, msg_id, text) for the LAST
    such line in file order, or None. `index` is the row's position in `rows`, needed to scan forward from it
    for a ledger.py call same as the old user/assistant path did."""
    last = None
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            continue
        for s in _iter_strings(row):
            if DISCORD_DM_HEADER not in s:
                continue
            for m in DISCORD_DM_LINE.finditer(s):
                last = (i, m.group(1), m.group(2).strip())
    return last


def looks_like_correction(text):
    """Pure. True when `text` matches the shared correction regex."""
    return bool(CORRECT.search(text or ""))


def ledger_call_in_turn(calls):
    """Pure. True when any Bash/PowerShell command in `calls` ([(name, input), ...]) already ran `ledger.py add`."""
    for name, inp in calls or []:
        if name in ("Bash", "bash", "PowerShell"):
            cmd = (inp or {}).get("command", "")
            if LEDGER_CALL.search(cmd):
                return True
    return False


def _blocks(row):
    c = (row.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _ledger_call_from(all_rows, start_idx):
    """Pure. True when any assistant Bash/PowerShell tool_use at or after `start_idx` in `all_rows`
    (the full, unfiltered row list) already ran `ledger.py add`."""
    calls = []
    for r in all_rows[start_idx:]:
        if not isinstance(r, dict) or r.get("type") != "assistant":
            continue
        for b in _blocks(r):
            if b.get("type") == "tool_use" and b.get("name") in ("Bash", "bash", "PowerShell"):
                calls.append((b.get("name"), b.get("input") or {}))
    return ledger_call_in_turn(calls)


def _find_last_typed_user_row(rows):
    """Pure. Scan the FULL, unfiltered row list (original indices, so they stay comparable against
    find_last_discord_message's indices) for the last non-synthetic "type": "user" row.
    -> (index, msg_id, text) or None."""
    last = None
    for i, r in enumerate(rows):
        if not isinstance(r, dict) or r.get("type") != "user":
            continue
        content = (r.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in _blocks(r) if b.get("type") == "text")
        if text.strip() and not SYNTHETIC_WRAPPER.match(text) and not SYNTHETIC_SKILL_BODY.match(text):
            msg_id = r.get("uuid") or r.get("id")
            if not msg_id:
                import hashlib

                msg_id = hashlib.sha1((text + str(r.get("timestamp", ""))).encode("utf-8", "replace")).hexdigest()[:10]
            last = (i, str(msg_id), text.strip())
    return last


def find_correction(rows):
    """Pure. Scan transcript rows (dicts) for the owner's last real message (whichever channel it actually
    came in on) and this turn's tool calls. -> (msg_id, text, had_ledger_call) or None if it doesn't look
    like a correction.

    2026-11-19, security review caught a real gap in the first version of this fix: it always preferred the
    Discord path over the direct-typed "type: user" path, so a real terminal-typed correction immediately
    followed by an unrelated, later Discord message (e.g. "ok") would be silently missed -- the Discord
    path's benign text would win regardless of which one actually happened last. Fixed by finding both
    candidates (over the same full, unfiltered row list so their indices are comparable) and keeping
    whichever is later in file order; only that one is checked for looks_like_correction. A non-correction
    beats a correction only when it is genuinely the more recent real message, not just because it came
    over a particular channel."""
    discord_hit = find_last_discord_message(rows)
    typed_hit = _find_last_typed_user_row(rows)
    if discord_hit is None and typed_hit is None:
        return None
    if typed_hit is None or (discord_hit is not None and discord_hit[0] >= typed_hit[0]):
        winner = discord_hit
    else:
        winner = typed_hit
    idx, msg_id, text = winner
    if not looks_like_correction(text):
        return None
    had_ledger_call = _ledger_call_from(rows, idx)
    return (str(msg_id), text[:200], had_ledger_call)


def _raise_correction_intent(msg_id, text):
    import intent_store

    what = (
        'CORRECTION not yet in the ledger: "%s". Log it now: python ledger.py add <this msg id or a short tag> '
        '"<what I got wrong>" "<the truth>" "<real cause, file or habit>" "<the fix>". Close this intent once the row exists, '
        "or --result skipped with why if it genuinely isn't a correction worth a row." % text
    )
    dedupe = "correction-%s" % msg_id
    cand = intent_store.new_intent("correction_unlogged", what, tier=1, value=3, dedupe=dedupe, from_event="correction")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(r"J:\Jarvis2\logs\correction_ledger_gate_off.txt"):
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
        found = find_correction(rows)
        if found is None:
            return
        msg_id, text, had_ledger_call = found
        if had_ledger_call:
            return
        _raise_correction_intent(msg_id, text)
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a gate bug
        return


if __name__ == "__main__":
    main()
