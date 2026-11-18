# -*- coding: utf-8 -*-
"""
Assistant-side watcher for the Archive Terminal's UI telemetry (features_py/ui_events.py in the jarvis-ui repo writes
J:\\Jarvis2\\logs\\ui_events.jsonl). Same byte-offset cursor pattern as ui_queue_check.py.

Prints ONLY what needs attention, grouped and collapsed, one line each:
  [UI-EVENT] ERROR x5: <text>                      new events with severity error (repeats summed)
  [UI-EVENT] BRIDGE-FAIL x4 <method>: <text>        a bridge call that failed >= 3 times in the new batch
  [UI-EVENT] ALERT x2: <text>                      toasts of kind 'alert' (the terminal's own warnings to the user)
Everything else (info, tool opens, screens, single bridge failures) is NOT pushed -- inspect it on demand with
`python ui_events.py recent|summary`.

Exit 0 and print when there is something new; exit 1 (silent) when there isn't. `--ack` commits the cursor.
combined_watcher.py imports pending()/CURSOR/Debouncer from here (20 s quiet-wait, non-blocking, so a burst becomes
one notification). Privacy: the log holds no content (see the 'What Jarvis sees' section of What to Ask Jarvis).
"""
import json
import os
import sys
import time
from pathlib import Path

EVENTS_LOG = Path(os.environ.get("UI_EVENTS_FILE") or r"J:\Jarvis2\logs\ui_events.jsonl")
CURSOR = Path(os.environ.get("UI_EVENTS_CURSOR") or r"J:\Jarvis2\logs\ui_events_cursor.txt")
DEBOUNCE_SECONDS = 20
MAX_WAIT_SECONDS = 120  # a continuous stream of errors still gets reported
BRIDGE_FAIL_MIN = 3


def get_cursor():
    try:
        return int(CURSOR.read_text().strip())
    except (OSError, ValueError):
        return 0


def read_events(start=0, path=None):
    """(events, end_offset) from `start`. Only complete lines are consumed; a half-written last line waits."""
    path = path or EVENTS_LOG
    if not path.exists():
        return [], 0
    size = path.stat().st_size
    if start > size:  # rotated or cleared
        start = 0
    with open(path, "rb") as f:
        f.seek(start)
        data = f.read()
    cut = data.rfind(b"\n")
    if cut < 0:
        return [], start
    events = []
    for raw in data[: cut + 1].splitlines():
        try:
            e = json.loads(raw.decode("utf-8", errors="replace"))
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("text"), str):
            events.append(e)
    return events, start + cut + 1


# flavour toasts the app shows on purpose (random "incident" easter eggs): not problems, never wake the assistant
IGNORED_ALERT_PREFIXES = ("INCIDENT:",)

def _n(e):
    n = e.get("n")
    return n if isinstance(n, int) and n >= 1 else 1


def _times(n):
    return f" x{n}" if n > 1 else ""


def summarize(events):
    """Grouped one-line summaries of the events that deserve a push."""
    errors, alerts, fails = {}, {}, {}
    order = []
    for e in events:
        kind, sev, tag, text = e.get("kind"), e.get("sev"), e.get("tag") or "", e["text"]
        if kind == "bridge-fail":
            g = fails.setdefault(tag or text, {"n": 0, "text": text})
            g["n"] += _n(e)
            if sev == "error":
                errors.setdefault(text, 0)
                errors[text] += _n(e)
                order.append(("err", text))
            elif ("fail", tag or text) not in order:
                order.append(("fail", tag or text))
        elif sev == "error":
            if text not in errors:
                order.append(("err", text))
            errors[text] = errors.get(text, 0) + _n(e)
        elif kind == "toast" and tag == "alert" and not text.upper().startswith(IGNORED_ALERT_PREFIXES):
            if text not in alerts:
                order.append(("alert", text))
            alerts[text] = alerts.get(text, 0) + _n(e)
    lines, seen = [], set()
    for what, key in order:
        if (what, key) in seen:
            continue
        seen.add((what, key))
        if what == "err":
            lines.append(f"ERROR{_times(errors[key])}: {key}")
        elif what == "alert":
            lines.append(f"ALERT{_times(alerts[key])}: {key}")
        elif what == "fail" and fails[key]["n"] >= BRIDGE_FAIL_MIN:
            lines.append(f"BRIDGE-FAIL{_times(fails[key]['n'])} {key}: {fails[key]['text']}")
    return lines


def pending():
    events, end = read_events(get_cursor())
    return summarize(events), end


def _log_size():
    try:
        return EVENTS_LOG.stat().st_size
    except OSError:
        return 0


class Debouncer:
    """Non-blocking quiet-wait: ready(size) is True once the log's size has been unchanged for `quiet` seconds (or `max_wait`
    seconds have passed since the first pending event), so a burst of events becomes ONE notification without stalling the
    other sources in combined_watcher's loop. Call reset() after reporting."""

    def __init__(self, quiet=DEBOUNCE_SECONDS, max_wait=MAX_WAIT_SECONDS, clock=time.time):
        self.quiet, self.max_wait, self.clock = quiet, max_wait, clock
        self.reset()

    def reset(self):
        self.first = self.changed = self.size = None

    def ready(self, size):
        now = self.clock()
        if self.first is None:
            self.first = self.changed = now
            self.size = size
        elif size != self.size:
            self.size, self.changed = size, now
        return now - self.changed >= self.quiet or now - self.first >= self.max_wait


def log_size():
    try:
        return EVENTS_LOG.stat().st_size
    except OSError:
        return 0


def main():
    if "--ack" in sys.argv:
        _, end = read_events(get_cursor())
        CURSOR.write_text(str(end), encoding="utf-8")
        print(f"cursor -> {end}")
        return 0
    lines, _ = pending()
    if lines:
        for ln in lines:
            print(f"[UI-EVENT] {ln}")
        return 0
    return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
