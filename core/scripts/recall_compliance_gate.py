# -*- coding: utf-8 -*-
"""Recall compliance gate, LOG-ONLY phase (2026-11-16).

Why: the owner's real complaint (today 10:46) was that recall_hook.py already injects [RECALL]
vault lines on a question-shaped prompt, but that a stated principle ("check memory before
replying") isn't applied consistently enough -- the block gets injected and I sometimes still
just answer from the conversation. recall_hook.py is passive (it surfaces context); nothing
checked whether a turn that got a [RECALL] block actually showed any sign of using it.

Same shape as claim_gate.py on purpose (2026-10-29's own plan: log-only baseline first, then
decide whether a blocking mode is worth it) -- reuse a proven pattern instead of growing a
divergent one (feedback_patch_not_root_cause_2026_11_12.md).

Stop hook: reads the hook JSON from stdin (transcript_path), finds the last turn's end time,
checks recall_log.jsonl for an entry injected during that turn, and if one exists, checks the
final reply text for any engagement signal (quoting a note name, saying checked/confirmed/
verified/outdated/per vault, or reading like a direct answer that plausibly used it). Never
blocks, never prints. Output: logs/recall_compliance.jsonl, one line per turn where a RECALL
block was injected, flagged True when no engagement signal was found.
Limits: regex engagement detection cannot tell whether the recalled fact was truly load-bearing
in the answer, only whether the reply shows a textual sign of having looked. A turn with no
question-shaped prompt never gets a RECALL block and is correctly absent from this log, not
flagged -- this gate only covers turns the existing hook already decided were worth searching.
Testable core: analyze(rows, recall_entries) -> record dict or None."""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
RECALL_LOG = os.path.join(LOGS, "recall_log.jsonl")
OUT = os.path.join(LOGS, "recall_compliance.jsonl")
WINDOW_SECONDS = 600  # a recall injected any time in the last 10 min of a turn counts as this turn's

ENGAGEMENT = re.compile(
    r"\b(checked|confirmed|verified|according to|per the vault|per memory|memory says|"
    r"the (?:note|vault|memory) says|that'?s (?:outdated|stale|still true|still accurate)|"
    r"last time (?:we|i)|previously (?:we|i|found|decided)|re-?verified|double-?checked)\b", re.I)


def _is_synthetic(text):
    try:
        from correction_ledger_gate import SYNTHETIC_WRAPPER, SYNTHETIC_SKILL_BODY
    except Exception:  # noqa: BLE001
        SYNTHETIC_WRAPPER = re.compile(r"^\s*<[A-Za-z][\w:-]*(?:\s[^<>]*)?/?>")
        SYNTHETIC_SKILL_BODY = re.compile(r"^\s*\(Re-invocation of /|^\s*Base directory for this skill:", re.I)
    return bool(SYNTHETIC_WRAPPER.match(text) or SYNTHETIC_SKILL_BODY.match(text))


def blocks(row):
    c = (row.get("message") or {}).get("content")
    return c if isinstance(c, list) else []


def _row_ts(row):
    """-> epoch seconds from a transcript row's timestamp field, or None."""
    ts = row.get("timestamp")
    if not ts:
        return None
    try:
        import datetime
        return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def _parse_log_ts(s):
    try:
        return time.mktime(time.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return None


def find_recall_for_turn(recall_entries, turn_start_epoch, turn_end_epoch):
    """Pure. -> the most relevant recall_log.jsonl entry whose timestamp falls inside
    [turn_start_epoch, turn_end_epoch + WINDOW_SECONDS], or None. Picks the latest match."""
    if turn_start_epoch is None:
        return None
    best = None
    lo = turn_start_epoch - 5  # small slack: the hook can log a hair before the row's own stamp
    hi = (turn_end_epoch or turn_start_epoch) + WINDOW_SECONDS
    for e in recall_entries or []:
        t = _parse_log_ts(e.get("ts", ""))
        if t is None:
            continue
        if lo <= t <= hi:
            if best is None or t > best[0]:
                best = (t, e)
    return best[1] if best else None


def analyze(rows, recall_entries):
    """Return the compliance record for the last turn in `rows`, or None when no RECALL block
    was injected for that turn (nothing to check)."""
    rows = [r for r in rows if isinstance(r, dict) and r.get("type") in ("user", "assistant")]
    start = 0
    start_ts = None
    for i, r in enumerate(rows):
        content = (r.get("message") or {}).get("content")
        text = content if isinstance(content, str) else " ".join(b.get("text", "") for b in blocks(r) if b.get("type") == "text")
        if r["type"] == "user" and text.strip() and not _is_synthetic(text):
            start = i
            start_ts = _row_ts(r)
    end_ts = None
    last_text = ""
    for r in rows[start:]:
        if r["type"] == "assistant":
            t = _row_ts(r)
            if t is not None:
                end_ts = t
            for b in blocks(r):
                if b.get("type") == "text":
                    last_text = b.get("text", "")
    entry = find_recall_for_turn(recall_entries, start_ts, end_ts)
    if entry is None:
        return None
    engaged = bool(ENGAGEMENT.search(last_text))
    return {
        "recall_q": entry.get("q", "")[:120],
        "recall_lines": entry.get("lines", 0),
        "engaged": engaged,
        "flagged": not engaged,
        "text_head": last_text[:120].replace("\n", " "),
    }


def _load_recall_entries():
    out = []
    try:
        with open(RECALL_LOG, encoding="utf-8", errors="ignore") as f:
            for line in f.readlines()[-50:]:
                try:
                    out.append(json.loads(line))
                except Exception:
                    pass
    except OSError:
        pass
    return out


def main():
    try:
        if os.environ.get("JARVIS_FALLBACK"):
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
            except Exception:
                pass
        rec = analyze(rows, _load_recall_entries())
        if rec is None:
            return
        rec = dict({"ts": time.strftime("%F %T")}, **rec)
        with open(OUT, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        return


if __name__ == "__main__":
    main()
