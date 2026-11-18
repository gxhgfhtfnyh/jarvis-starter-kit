# -*- coding: utf-8 -*-
"""PostToolUse hook (Write|Edit|NotebookEdit): closes a real gap found in the Enterprise-Grade
Gap Check (2026-11-05) and confirmed still live tonight (2026-11-06) -- integrity_check.py
flags send_gate_hook.py, recall_hook.py, voice_hook.py, keep_going.py and intent_store.py as
[TAMPERED] whenever their content drifts from the last baseline, but nothing ever re-checked
automatically or told anyone -- the file just sat red until someone happened to run the checker
by hand. A benign same-session edit (what this usually is) and a real compromise look identical
to that checker, so this hook does NOT auto-rebaseline (that would silently bless a malicious
edit the moment it happens, defeating the whole point of integrity_check.py watching these
specific files). It only raises a tier-1 intent so a human eye lands on the file before it's
trusted again -- the actual logs/integrity_baseline.json update still needs
`python integrity_check.py --rebaseline` run deliberately, same as always.
"""
import json
import os
import re
import subprocess
import sys
import time

WATCHED = re.compile(r"(send_gate_hook|recall_hook|voice_hook|keep_going|intent_store)\.py$", re.I)
LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "integrity_watch.log")


def _log(line):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("%s: %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%S"), line))
    except OSError:
        pass


def extract_file_path(payload):
    tin = payload.get("tool_input", {})
    if not isinstance(tin, dict):
        return None
    return tin.get("file_path") or tin.get("notebook_path")


def run(payload, python_exe=None, script_dir=None):
    """-> (exit_code, message). Pure-ish aside from the subprocess call it makes when watched."""
    script_dir = script_dir or os.path.dirname(__file__)
    path = extract_file_path(payload)
    if not path or not WATCHED.search(path):
        return 0, ""
    basename = os.path.basename(path)
    dedupe_key = "integrity-watch-%s-%s" % (basename, time.strftime("%Y%m%d"))
    python_exe = python_exe or sys.executable
    try:
        subprocess.run(
            [python_exe, os.path.join(script_dir, "intent_store.py"), "add",
             "--kind", "integrity_watch",
             "--what", "%s was edited -- verify the change is benign, then run `python integrity_check.py --rebaseline` to clear it (it will otherwise keep showing [TAMPERED])" % basename,
             "--tier", "1", "--value", "4", "--dedupe", dedupe_key],
            capture_output=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass
    _log("edit detected on %s, raised intent (dedupe=%s)" % (basename, dedupe_key))
    return 0, ""


def main():
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw or "{}")
    except (ValueError, OSError):
        return 0
    try:
        code, msg = run(payload)
    except Exception:          # noqa: BLE001 - never block an edit over this hook's own bug
        return 0
    if msg:
        sys.stderr.write(msg + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
