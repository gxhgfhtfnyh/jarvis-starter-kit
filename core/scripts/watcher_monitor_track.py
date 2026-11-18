# -*- coding: utf-8 -*-
"""PostToolUse observer (2026-11-13, "force don't remember" fix): every time the live Discord/UI
watcher Monitor is (re)armed, records the epoch to logs/watcher_monitor_last_armed.txt. Paired with
watcher_monitor_gate.py (a Stop hook) so re-arming the Monitor is forced structurally instead of
relying on noticing the harness's own "Monitor expired" notice. Observe-only: never blocks, never
prints, silent on any error (same philosophy as avatar_ui_hook.py's other observers).

Registration (PostToolUse, matcher "Monitor"): python watcher_monitor_track.py
Testable core: is_watcher_arm(tool_name, tool_input) -> bool."""
import json
import sys
import time

OUT = r"J:\Jarvis2\logs\watcher_monitor_last_armed.txt"


def is_watcher_arm(tool_name, tool_input):
    """Pure. True when this call armed the combined_watcher_live.log Monitor specifically."""
    if tool_name != "Monitor":
        return False
    cmd = (tool_input or {}).get("command") or ""
    return "combined_watcher_live.log" in cmd


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except Exception:
        return
    if not is_watcher_arm(payload.get("tool_name"), payload.get("tool_input") or {}):
        return
    try:
        with open(OUT, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        pass


if __name__ == "__main__":
    main()
