# -*- coding: utf-8 -*-
"""Inspect EVERYTHING the Archive Terminal has reported about itself (info too), unlike ui_events_check.py which only
pushes errors/alerts. Source: J:\\Jarvis2\\logs\\ui_events.jsonl (+ the rotated .1 backup when --since reaches back).

Usage:
    python ui_events.py recent [--n 30] [--all] [--since 10m]   newest last; default hides tool/screen/lifecycle chatter
                                                                 unless --all; --since takes 30s, 10m, 2h, 1d
    python ui_events.py summary [--since 1h]                    counts per kind, top errors, tools opened, last screen

The log never holds note contents, typed text, chat text, search queries, bridge arguments/results, or secrets.
"""
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_events_check as chk  # noqa: E402

QUIET_KINDS = {"tool", "screen", "lifecycle", "skin", "theme", "login"}
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_since(text):
    """'10m' -> 600. None for empty; ValueError for garbage."""
    if not text:
        return None
    text = text.strip().lower()
    if text[-1] in _UNITS and text[:-1].isdigit():
        return int(text[:-1]) * _UNITS[text[-1]]
    raise ValueError(f"bad --since value {text!r} (use 30s, 10m, 2h, 1d)")


def load_all(path=None):
    """Every readable event, oldest first (the .1 backup first, then the live file)."""
    path = path or chk.EVENTS_LOG
    out = []
    for p in (path.with_name(path.name + ".1"), path):
        events, _ = chk.read_events(0, p)
        out.extend(events)
    return out


def select(events, since=None, now=None):
    if since is None:
        return events
    cutoff = (time.time() if now is None else now) - since
    return [e for e in events if isinstance(e.get("ts"), (int, float)) and e["ts"] >= cutoff]


def format_event(e):
    ts = e.get("ts")
    stamp = time.strftime("%H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "--:--:--"
    n = chk._n(e)
    return f"{stamp} {str(e.get('sev', 'info')).upper():5} {str(e.get('kind', '?')):11} {e['text']}{f' x{n}' if n > 1 else ''}"


def recent(events, n=30, show_all=False):
    pool = events if show_all else [e for e in events if e.get("kind") not in QUIET_KINDS or e.get("sev") != "info"]
    return [format_event(e) for e in pool[-max(1, n):]]


def summary(events):
    if not events:
        return ["no events"]
    kinds = Counter()
    errors = Counter()
    tools = Counter()
    last_screen = None
    for e in events:
        n = chk._n(e)
        kinds[e.get("kind", "?")] += n
        if e.get("sev") == "error" or e.get("kind") == "bridge-fail" or (e.get("kind") == "toast" and e.get("tag") == "alert"):
            errors[f"{e.get('kind')}: {e['text']}"] += n
        if e.get("kind") == "tool":
            tools[e.get("tag") or e["text"]] += n
        if e.get("kind") == "screen":
            last_screen = e["text"]
    out = [f"events: {sum(kinds.values())} ({len(events)} records)", "by kind: " + ", ".join(f"{k} {v}" for k, v in kinds.most_common())]
    out.append("top errors: " + ("none" if not errors else ""))
    out.extend(f"  x{v} {k}" for k, v in errors.most_common(5))
    out.append("tools opened: " + (", ".join(f"{k} x{v}" for k, v in tools.most_common(10)) or "none"))
    out.append(f"last screen: {last_screen or 'unknown'}")
    first, last = events[0].get("ts"), events[-1].get("ts")
    if isinstance(first, (int, float)) and isinstance(last, (int, float)):
        out.append(f"span: {time.strftime('%Y-%m-%d %H:%M', time.localtime(first))} -> {time.strftime('%H:%M', time.localtime(last))}")
    return out


def _arg(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        if i + 1 < len(argv):
            return argv[i + 1]
    return default


def main(argv):
    if not argv or argv[0] not in ("recent", "summary"):
        print(__doc__)
        return 1
    try:
        since = parse_since(_arg(argv, "--since"))
        n = int(_arg(argv, "--n", "30"))
    except ValueError as e:
        print(e)
        return 1
    events = select(load_all(), since)
    lines = recent(events, n, "--all" in argv) if argv[0] == "recent" else summary(events)
    print("\n".join(lines) if lines else "no events")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
