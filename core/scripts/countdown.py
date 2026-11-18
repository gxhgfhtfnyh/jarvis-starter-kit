# -*- coding: utf-8 -*-
"""
Item 9 of the 2026-08-09 tools brainstorm: a generic "days until X" for any
named event, not just the exam/birthday countdowns already baked into the
avatar's own day-context logic. A tiny persisted list of named target dates.

Usage:
    python countdown.py add "G133 CA due" 2026-08-25
    python countdown.py list
    python countdown.py remove "G133 CA due"
"""
import datetime as dt
import json
import sys
from pathlib import Path

STORE_PATH = Path(r"J:\Jarvis2\countdowns.json")


def _load():
    if not STORE_PATH.exists():
        return {}
    try:
        return json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(data):
    STORE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def add_countdown(name, date_str):
    data = _load()
    data[name] = date_str
    _save(data)


def remove_countdown(name):
    data = _load()
    data.pop(name, None)
    _save(data)


def list_countdowns():
    data = _load()
    today = dt.date.today()
    results = []
    for name, date_str in data.items():
        try:
            d = dt.date.fromisoformat(date_str)
        except ValueError:
            continue
        results.append({"name": name, "date": d, "days_left": (d - today).days})
    return sorted(results, key=lambda r: r["days_left"])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: add <name> <YYYY-MM-DD> | remove <name> | list")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "add":
        add_countdown(sys.argv[2], sys.argv[3])
        print("added")
    elif cmd == "remove":
        remove_countdown(sys.argv[2])
        print("removed")
    elif cmd == "list":
        for r in list_countdowns():
            tag = "TODAY" if r["days_left"] == 0 else ("PAST" if r["days_left"] < 0 else f"{r['days_left']}d")
            print(f"[{tag}] {r['name']} ({r['date']})")
