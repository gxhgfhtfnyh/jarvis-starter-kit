# -*- coding: utf-8 -*-
"""
Enterprise-grade gap #4 (Enterprise-Grade Gap Check, Nov 5 2026): nothing
stopped a hook/safety-script edit from reaching the live settings.json
without its tests having been run in that same turn. the owner's call
(AskUserQuestion, 2026-11-05): enforce it for real, not just nudge.

Three modes, wired as three separate hook entries in ~/.claude/settings.json:
  record  -- PostToolUse, matcher Edit|Write|NotebookEdit: if the edited file
             is one of the critical files integrity_check.py already watches
             (hook scripts wired in settings.json, CLAUDE.md, settings.json
             itself), mark it pending in the state file.
  clear   -- PostToolUse, matcher Bash: if the command ran pytest against a
             test file whose name contains the pending file's stem, and the
             output shows a pass (has "passed", no "FAILED"), clear it.
  check   -- Stop: if any critical file is still pending, block with a clear
             instruction naming the file and its likely test file(s).

Deliberately loose on test-file matching (substring on the stem, not an
exact 1:1 name) because several critical files have multiple real test
files (send_gate_hook.py has test_send_gate_hook.py, test_send_gate_e2e.py,
test_send_gate_precision.py, test_send_gate_redteam.py,
test_send_gate_review3.py) -- any one of them running green is real signal
the edit was checked, not a loophole.

Kill switch: logs/hook_test_gate_off.txt (skip everything silently).
State: logs/hook_test_gate_state.json. Log: logs/hook_test_gate.jsonl.
Reuses integrity_check.py's own critical-file list so this never drifts out
of sync with what that gate already watches -- one definition of "critical",
two different checks (tamper vs tested) on the same set.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import integrity_check as ic  # noqa: E402

LOGS = Path(r"J:\Jarvis2\logs")
STATE_PATH = LOGS / "hook_test_gate_state.json"
LOG_PATH = LOGS / "hook_test_gate.jsonl"
OFF_PATH = LOGS / "hook_test_gate_off.txt"


def _critical_basenames():
    """Script files (not CLAUDE.md/settings.json themselves -- those have no single test file) from the same set integrity_check.py watches."""
    out = set()
    for f in ic._critical_files():
        name = Path(f).name
        if name.lower().endswith((".py", ".ps1")):
            out.add(name)
    return out


def _stem(basename):
    return re.sub(r"\.(py|ps1)$", "", basename, flags=re.I)


def _match_key(basename):
    """A shorter match key than the full stem, so test_send_gate_redteam.py still counts as a real test run for
    send_gate_hook.py (stem send_gate_hook) without needing the exact '_hook' suffix in the test's own name -- the
    first two underscore-tokens of the stem (e.g. 'send_gate') is specific enough to avoid false matches while
    covering the real multi-test-file naming pattern already used in this codebase."""
    stem = _stem(basename)
    parts = stem.split("_")
    return "_".join(parts[:2]) if len(parts) > 2 else stem


def _load_state():
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"pending": {}}


def _save_state(state):
    STATE_PATH.write_text(json.dumps(state), encoding="utf-8")


def _log(entry):
    try:
        entry = dict(entry)
        entry["ts"] = time.strftime("%F %T")
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def record(payload):
    """Pure-ish core: given the PostToolUse payload, return the basename to mark pending, or None."""
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path", "")
    name = Path(str(file_path)).name
    if name in _critical_basenames() and not name.lower().startswith("test_"):
        return name
    return None


def clear_matches(command_text, response_text, pending):
    """Pure core: which pending basenames does this Bash call's command+response clear? Needs pytest in the command, this
    file's stem as a substring of the command (so we only credit a test run that actually targeted this file's area), and
    a passing-looking response (has 'passed', no 'FAILED')."""
    cmd = (command_text or "").lower()
    resp = response_text or ""
    if "pytest" not in cmd:
        return []
    if "failed" in resp.lower() and "0 failed" not in resp.lower():
        return []
    if "passed" not in resp.lower():
        return []
    cleared = []
    for name in list(pending):
        key = _match_key(name).lower()
        if key and key in cmd:
            cleared.append(name)
    return cleared


def main():
    if OFF_PATH.exists():
        return
    if os.environ.get("HERDR_ENV"):   # a team-member pane, not the live orchestrator - file-change test gating is Jarvis-identity work only (2026-11-09, caught derailing claude1 onto an edit it never made)
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    if mode == "record":
        name = record(payload)
        if name:
            state = _load_state()
            state.setdefault("pending", {})[name] = True
            _save_state(state)
            _log({"event": "pending", "file": name})

    elif mode == "clear":
        state = _load_state()
        pending = state.get("pending", {})
        if not pending:
            return
        tool_input = payload.get("tool_input") or {}
        command_text = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
        response_text = json.dumps(payload.get("tool_response") or "")
        cleared = clear_matches(command_text, response_text, pending)
        if cleared:
            for name in cleared:
                pending.pop(name, None)
                _log({"event": "cleared", "file": name})
            _save_state(state)

    elif mode == "check":
        state = _load_state()
        pending = state.get("pending", {})
        if pending:
            names = sorted(pending)
            msg = ("You edited a critical/hook-wired file this turn without a passing test run for it: " + ", ".join(names) +
                   ". Run its test file (pytest -q scripts/test_<name-without-.py-or-_hook>*.py) now with a tool call and let it pass before "
                   "stopping. If no test file exists yet for one of these, write one now -- a hook/safety-script this core should not ship untested.")
            print(json.dumps({"decision": "block", "reason": msg}))
            _log({"event": "blocked", "files": names})


if __name__ == "__main__":
    main()
