# -*- coding: utf-8 -*-
"""Read and write the Archive Terminal's kv data (habits, commitments, quests...) without hand-editing JSON.
Same rules as features_py/kv_store.py: name = a-z 0-9 _ - (1-40 chars, starts with a letter, no Windows device
names), value under 200 KB, atomic write, files live in $ARCHIVE_DATA_DIR (default ~/jarvis-ui/.data).

Usage:
    python ui_data.py get <kvname>                 # prints the JSON value (null if absent)
    python ui_data.py set <kvname> --stdin         # replaces the value with JSON from stdin
    python ui_data.py add <kvname> --stdin         # appends JSON from stdin to a list (created if absent)
    python ui_data.py add <kvname> --stdin --max N # ...and keeps only the newest N items

Read a name's current shape with `get` first so an added item matches what its screen expects. Pipe JSON via a
heredoc (quotes survive). `add` refuses a name whose current value is not a list, and refuses anything that would
push the file past 200 KB (it never silently drops data unless --max is given).
"""
import json
import os
import re
import sys
import threading
from pathlib import Path

_NAME = re.compile(r"[a-z][a-z0-9_-]{0,39}")
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}
MAX_BYTES = 200_000
_LOCK = threading.Lock()


def valid_name(name):
    return isinstance(name, str) and _NAME.fullmatch(name) is not None and name not in _RESERVED


def data_dir():
    return Path(os.environ.get("ARCHIVE_DATA_DIR") or (Path.home() / "jarvis-ui" / ".data"))


def _path(name):
    return data_dir() / f"{name}.json"


def kv_get(name):
    if not valid_name(name):
        return {"ok": False, "error": "bad name"}
    path = _path(name)
    try:
        if path.is_symlink():
            return {"ok": True, "value": None}  # never follow a planted link
        return {"ok": True, "value": json.loads(path.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"ok": True, "value": None}


def _write(name, value):
    try:
        data = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"ok": False, "error": "value is not JSON-serialisable"}
    if len(data.encode("utf-8")) > MAX_BYTES:
        return {"ok": False, "error": "value too large (200 KB max)"}
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.replace(path)
    return {"ok": True}


def kv_set(name, value):
    if not valid_name(name):
        return {"ok": False, "error": "bad name"}
    with _LOCK:
        if value is None:
            try:
                _path(name).unlink()
            except FileNotFoundError:
                pass
            return {"ok": True}
        return _write(name, value)


def kv_add(name, item, max_items=None):
    if not valid_name(name):
        return {"ok": False, "error": "bad name"}
    with _LOCK:
        cur = kv_get(name)["value"]
        if cur is None:
            cur = []
        if not isinstance(cur, list):
            return {"ok": False, "error": "existing value is not a list"}
        cur = cur + [item]
        if max_items is not None and max_items > 0:
            cur = cur[-max_items:]
        return _write(name, cur)


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if len(argv) < 2 or argv[0] not in ("get", "set", "add"):
        print(__doc__)
        return 1
    op, name, rest = argv[0], argv[1], argv[2:]
    if op == "get":
        r = kv_get(name)
        if not r["ok"]:
            print("ERROR:", r["error"])
            return 1
        print(json.dumps(r["value"], ensure_ascii=False, indent=2))
        return 0
    if "--stdin" not in rest:
        print("ERROR: pass the JSON via --stdin")
        return 1
    max_items = None
    if "--max" in rest:
        try:
            max_items = int(rest[rest.index("--max") + 1])
        except (ValueError, IndexError):
            print("ERROR: --max needs a number")
            return 1
    try:
        value = json.loads(sys.stdin.read())
    except ValueError as e:
        print(f"ERROR: stdin is not valid JSON ({e})")
        return 1
    r = kv_set(name, value) if op == "set" else kv_add(name, value, max_items)
    print("OK" if r["ok"] else f"ERROR: {r['error']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
