# -*- coding: utf-8 -*-
"""Undo the last presence change (run by Jarvis when the owner replies "undo" to an auto-switch DM).

    python presence_apply.py undo     # set the mode back to what it was before the last change
    python presence_apply.py status   # show the last change (default)
The undo goes through set_presence_mode.py as a normal (manual) change, so it also starts the 2 h auto-switch pause
(presence_auto.py). Refuses if the last change is older than 2 h or there is nothing to undo.
"""
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import presence_auto as P  # noqa: E402


def apply(answer, now_ts=None, setter=None, current=None):
    now_ts = time.time() if now_ts is None else now_ts
    ch = P._read_json(P.CHANGE_FILE, {})
    if answer == "status":
        return f"last change: {ch}" if ch else "no recorded change"
    if answer != "undo":
        return "usage: presence_apply.py undo|status"
    prev = ch.get("prev")
    if not ch or prev not in ("home", "away", "asleep"):
        return "nothing to undo"
    if now_ts - float(ch.get("ts", 0)) > P.OVERRIDE_S:
        return "last change is older than 2h: not undoing (set the mode explicitly instead)"
    cur = current if current is not None else P.get_mode()
    if cur != ch.get("mode"):
        return f"mode is {cur}, not {ch.get('mode')}: not undoing (set the mode explicitly instead)"
    if setter is None:
        r = subprocess.run([sys.executable, str(HERE / "set_presence_mode.py"), prev], capture_output=True, text=True,
                           timeout=60)
        if r.returncode != 0:
            return f"set_presence_mode failed: {(r.stdout + r.stderr).strip()}"
    else:
        setter(prev)
    return f"undone: mode back to {prev}; auto-switching paused 2h"


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(apply(sys.argv[1].lower() if len(sys.argv) > 1 else "status"))
