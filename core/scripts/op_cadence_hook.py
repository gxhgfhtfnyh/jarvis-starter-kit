# -*- coding: utf-8 -*-
"""PreToolUse hook (Bash|Edit|Write|Agent|Task): keeps the paper trail on a running operation fresh.

While any card ACTION is open (sent / acknowledged / working) and the newest entry on its operation file is older than 8 minutes
- or 12 tool calls have passed since the last entry - this prints a hookSpecificOutput.additionalContext telling the assistant to
file a numbered step and an interim progress report right now (exact commands and the action id). Throttled to once per 4 minutes;
silent otherwise. Never writes to the vault, never fails the tool call (always exits 0).

Env overrides (tests): ARCHIVE_ACTIONS_FILE, ARCHIVE_OPS_FILE, JARVIS_CADENCE_STATE, JARVIS_CADENCE_NOW (epoch seconds).
Reads the hook JSON on stdin (ignored apart from a sanity parse). State lives in J:\\Jarvis2\\logs\\op_cadence_state.json.
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ACTIONS = Path(os.environ.get("ARCHIVE_ACTIONS_FILE") or r"J:\Jarvis2\logs\ui_actions.json")
OPS = Path(os.environ.get("ARCHIVE_OPS_FILE") or r"J:\Jarvis2\logs\ui_operations.json")
STATE = Path(os.environ.get("JARVIS_CADENCE_STATE") or r"J:\Jarvis2\logs\op_cadence_state.json")
OPEN = ("sent", "acknowledged", "working")
STALE_ACTION_S = 6 * 3600   # an open action older than this no longer counts (same as features_py/actions.py)
MAX_AGE_S = 8 * 60          # newest entry older than this -> nudge
MAX_CALLS = 12              # tool calls since the last entry -> nudge
THROTTLE_S = 4 * 60         # at most one nudge per this many seconds


def _now():
    try:
        return float(os.environ["JARVIS_CADENCE_NOW"])
    except (KeyError, ValueError):
        return time.time()


def _read(path, default):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if isinstance(data, type(default)) else default
    except (OSError, ValueError):
        return default


def _epoch(iso):
    try:
        return dt.datetime.fromisoformat(str(iso)).timestamp()
    except (TypeError, ValueError):
        return None


def _norm(title):
    return " ".join(str(title or "").split()).lower()[:120]


def open_cards(now):
    """[(action_id, title, created_epoch)] for fresh open card actions."""
    out = []
    for a in _read(ACTIONS, {"actions": []}).get("actions", []):
        if not isinstance(a, dict) or a.get("kind") != "card" or a.get("status") not in OPEN:
            continue
        created = _epoch(a.get("created"))
        if created is not None and now - created > STALE_ACTION_S:
            continue
        out.append((str(a.get("id")), str(a.get("ref") or a.get("title") or ""), created or now))
    return out


def newest_entry(action_id, title):
    """Epoch of the newest filed entry (not the owner's own notes) on the card's operation file, or None."""
    ops = _read(OPS, {"ops": []}).get("ops", [])
    key = _norm(title)
    best = None
    for op in ops:
        if not isinstance(op, dict) or op.get("state") == "past":
            continue
        if op.get("action") != action_id and _norm(op.get("key") or op.get("title")) != key:
            continue
        for e in op.get("entries", []):
            if isinstance(e, dict) and e.get("kind") != "owner-note":
                ts = _epoch(e.get("ts"))
                if ts is not None and (best is None or ts > best):
                    best = ts
    return best


def decide(now, state):
    """(message or '', new_state). Pure apart from file reads."""
    cards = open_cards(now)
    calls = state.get("calls", {}) if isinstance(state.get("calls"), dict) else {}
    seen = state.get("seen", {}) if isinstance(state.get("seen"), dict) else {}
    last_fire = float(state.get("last_fire", 0) or 0)
    due = []
    new_calls, new_seen = {}, {}
    for action_id, title, created in cards:
        newest = newest_entry(action_id, title)
        stamp = newest if newest is not None else created
        n = int(calls.get(action_id, 0)) + 1
        if seen.get(action_id) != stamp:  # a new entry was filed: the tool-call count starts over
            n = 1
        new_calls[action_id], new_seen[action_id] = n, stamp
        age = now - stamp
        if age > MAX_AGE_S or n >= MAX_CALLS:
            due.append((action_id, title, age, n))
    new_state = {"calls": new_calls, "seen": new_seen, "last_fire": last_fire}
    if not due or now - last_fire < THROTTLE_S:
        return "", new_state
    new_state["last_fire"] = now
    parts = []
    for action_id, title, age, n in due:
        mins = int(age // 60)
        parts.append(
            f"{action_id} ({title[:50]}): {mins} min, {n} calls")
    msg = ("[PAPER TRAIL DUE] " + "; ".join(parts) + ". File now (plain English for the owner, no jargon): a numbered step "
           f"`op_note.py {due[0][0]} \"<full sentence>\" --kind step --target \"<what>\" --result \"<outcome>\"` and an interim update "
           f"`op_note.py {due[0][0]} --stdin --kind update` (CURRENT FOCUS / COMPLETED / IN PROGRESS / NEXT / RISKS). Then continue.")
    return msg, new_state


def summarize_calls(calls):
    """Pure. calls: [(tool name, input dict)] -> one plain sentence of what was done, built from the tool trace (2026-10-30: the paper trail should write itself;
    he should not have to say "remember to log your work", and neither should a hook have to nag me for it)."""
    if not calls:
        return ""
    edited, shell, tests, sends, agents = [], 0, 0, 0, 0
    for name, tin in calls:
        tin = tin if isinstance(tin, dict) else {}
        if name in ("Edit", "Write", "NotebookEdit"):
            f = os.path.basename(str(tin.get("file_path", "")).replace("\\", "/"))
            if f and f not in edited:
                edited.append(f)
        elif name in ("Bash", "PowerShell", "bash"):
            shell += 1
            cmd = str(tin.get("command", ""))
            tests += 1 if "pytest" in cmd else 0
            sends += 1 if "discord_queue_send" in cmd else 0
        elif name in ("Agent", "Task"):
            agents += 1
    parts = ["%d tool calls" % len(calls)]
    if edited:
        parts.append("changed %d file(s): %s%s" % (len(edited), ", ".join(edited[:8]), " and more" if len(edited) > 8 else ""))
    if shell:
        parts.append("%d shell commands (%d test runs, %d Discord messages)" % (shell, tests, sends))
    if agents:
        parts.append("%d helper agent(s)" % agents)
    return "Automatic trail: " + "; ".join(parts) + "."


def recent_calls(transcript_path, limit=60):
    """The last `limit` tool calls in the session transcript as (name, input) pairs; [] when it cannot be read."""
    out = []
    try:
        with open(transcript_path, "rb") as f:                   # only the tail: a session transcript can be many megabytes and this runs on a tool call
            f.seek(0, 2)
            start = max(0, f.tell() - 2_000_000)
            f.seek(start)
            lines = f.read().decode("utf-8", errors="ignore").splitlines()
            if start > 0:
                lines = lines[1:]                                       # the first line of a mid-file slice may be cut in half
    except (OSError, TypeError):
        return []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        content = (row.get("message") or {}).get("content") if isinstance(row, dict) else None
        if row.get("type") == "assistant" and isinstance(content, list):
            for b in content:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    out.append((b.get("name"), b.get("input") or {}))
    return out[-limit:]


def auto_file(msg, transcript_path):
    """When a nudge is due, file the step myself from the tool trace instead of asking. -> True only
    if EVERY due card in msg was filed. Real starvation bug found 2026-11-06: this used to re.match
    (not findall) only the FIRST action id, file only that one, then the caller wiped the whole
    message on any success - so with 2+ cards overdue at once, the second card never got filed AND
    never got surfaced, indefinitely, as long as the first kept succeeding each cycle. Now files
    every due card named in msg and only reports success when none were left out."""
    ids = re.findall(r"(a_[0-9a-f]+) \(", msg or "")
    if not ids or os.environ.get("JARVIS_CADENCE_NOW"):
        return False
    summary = summarize_calls(recent_calls(transcript_path))
    if not summary:
        return False
    all_ok = True
    for action_id in ids:
        try:
            r = subprocess.run([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "op_note.py"), action_id, summary, "--kind", "step", "--target", "automatic trail",
                                "--result", "filed from the tool trace"], capture_output=True, text=True, timeout=30)
            all_ok = all_ok and r.returncode == 0
        except Exception:
            all_ok = False
    return all_ok


def main():
    try:
        payload = {}
        try:
            payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
        except Exception:
            pass
        now = _now()
        state = _read(STATE, {})
        msg, new_state = decide(now, state)
        try:                                               # state first: a second hook process starting now must see last_fire and not file the same step again
            STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = STATE.with_name(STATE.name + ".tmp")
            tmp.write_text(json.dumps(new_state), encoding="utf-8")
            tmp.replace(STATE)
        except OSError:
            pass
        if msg and auto_file(msg, payload.get("transcript_path")):
            msg = ""                                       # filed for me: no nag
        if msg:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
