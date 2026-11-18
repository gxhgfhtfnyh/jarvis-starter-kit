#!/usr/bin/env python3
"""team_status_cli.py - live status of the Hermes+Herdr team.

Run:
    python team_status_cli.py                  one-off report
    python team_status_cli.py --watch          redraw every 5 s (Ctrl+C to stop)
    python team_status_cli.py --watch --interval 2
    python team_status_cli.py --json           machine-readable output

Reads live pane state from `herdr pane list` and fanout/task history from
J:\\Jarvis2\\logs\\team_queue.db (opened read-only). Optional settings live in
J:\\Jarvis2\\logs\\team_status_config.json; errors append to
J:\\Jarvis2\\logs\\team_status_errors.jsonl.

Provenance of the parts (see the report that accompanied this file):
  get_live_panes   hermes1, verbatim
  format_report    hermes-omni, verbatim
  README text      worker1 (flags/config/troubleshooting matched here)
  everything else  written by Claude, because the other assigned parts
                   (parse_args, TTL cache, log_error, load_config, tests)
                   never arrived in a usable form.
"""
import argparse
import functools
import json
import sqlite3
import subprocess
import sys
import time
from typing import Any

DB_PATH = r"J:\Jarvis2\logs\team_queue.db"
CONFIG_PATH = r"J:\Jarvis2\logs\team_status_config.json"
ERROR_LOG = r"J:\Jarvis2\logs\team_status_errors.jsonl"

DEFAULTS = {"interval": 5.0, "cache_ttl": 3.0, "history_limit": 10, "db_path": DB_PATH}


# ---------------------------------------------------------------- hermes1
def get_live_panes() -> list[dict[str, Any]]:
    """Run herdr pane list and return parsed panes as list of dicts."""
    result = subprocess.run(
        ["herdr", "pane", "list"],
        capture_output=True,
        text=True,
        encoding="utf-8",  # FIX: herdr emits UTF-8; default cp1252 crashed on Windows
        errors="replace",
        check=True,
    )
    data = json.loads(result.stdout)
    panes = data.get("result", {}).get("panes", [])
    return [
        {
            "pane_id": p.get("pane_id", ""),
            "label": p.get("label", ""),
            "agent": p.get("agent", ""),
            "agent_status": p.get("agent_status", ""),
            "workspace_id": p.get("workspace_id", ""),
        }
        for p in panes
    ]


# ------------------------------------------------------------ hermes-omni
def format_report(panes, history):
    """Render iterables of pane and history dictionaries as plain text."""
    panes = list(panes)
    history = list(history)

    def cell(value):
        if value is None:
            return "-"
        return " ".join(str(value).split()) or "-"

    def table(rows, keys, headers):
        values = [[cell(row.get(key)) for key in keys] for row in rows]
        widths = [
            max([len(title)] + [len(row[i]) for row in values])
            for i, title in enumerate(headers)
        ]

        def line(cells):
            return "  ".join(
                value.ljust(width)
                for value, width in zip(cells, widths)
            ).rstrip()

        return "\n".join(
            [line(headers), line(["-" * width for width in widths])]
            + [line(row) for row in values]
        )

    pane_section = table(
        panes,
        ["pane_id", "label", "agent", "agent_status", "workspace_id"],
        ["PANE", "LABEL", "AGENT", "STATUS", "WORKSPACE"],
    ) if panes else "No live panes."

    # Discover history columns without assuming a database schema.
    history_keys = list(dict.fromkeys(key for row in history for key in row))
    if history and history_keys:
        history_section = table(
            history,
            history_keys,
            [cell(key).upper() for key in history_keys],
        )
    else:
        history_section = (
            "No history fields." if history else "No task history."
        )

    return "\n\n".join([
        "TEAM STATUS",
        "LIVE PANES\n" + pane_section + f"\nTotal panes: {len(panes)}",
        "TASK HISTORY\n" + history_section
        + f"\nTotal history records: {len(history)}",
    ])


# ------------------------------------------------- written by Claude below
def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="Live Hermes+Herdr team status.")
    ap.add_argument("--watch", action="store_true", help="redraw in a loop")
    ap.add_argument("--interval", type=float, default=None, metavar="N",
                    help="seconds between refreshes in --watch mode")
    ap.add_argument("--json", action="store_true", help="print JSON, not text")
    args = ap.parse_args(argv)
    if args.interval is not None and args.interval <= 0:
        ap.error("--interval must be > 0")
    return args


def load_config(path=CONFIG_PATH):
    cfg = dict(DEFAULTS)
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            for k in DEFAULTS:
                if k in user:
                    cfg[k] = user[k]
    except FileNotFoundError:
        pass
    except (OSError, ValueError) as e:
        log_error(e, "load_config")
    return cfg


def log_error(exc, context, path=ERROR_LOG):
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "context": context,
           "type": type(exc).__name__, "error": str(exc)}
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass  # logging must never crash the tool


def ttl_cache(seconds):
    """Reuse the last result of a no-arg function for `seconds`."""
    def deco(fn):
        state = {"t": None, "v": None}

        @functools.wraps(fn)
        def wrapper():
            now = time.monotonic()
            if state["t"] is None or now - state["t"] >= seconds:
                state["v"] = fn()  # exceptions are not cached
                state["t"] = now
            return state["v"]
        wrapper.clear = lambda: state.update(t=None, v=None)
        return wrapper
    return deco


def _clip(s, n=40):
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def read_history(db_path, limit):
    """Recent fanouts + tasks from team_queue.db, opened read-only."""
    con = sqlite3.connect("file:" + db_path.replace("\\", "/") + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = []
        for r in con.execute(
            "SELECT 'fanout' AS kind, id, lead_pane, status, question AS text, created_at "
            "FROM fanouts"):
            rows.append(dict(r))
        for r in con.execute(
            "SELECT 'task' AS kind, id, lead_pane, status, prompt AS text, created_at "
            "FROM tasks"):
            rows.append(dict(r))
    finally:
        con.close()
    rows.sort(key=lambda r: r["created_at"] or 0, reverse=True)
    out = []
    for r in rows[:limit]:
        out.append({
            "kind": r["kind"], "id": r["id"], "lead": r["lead_pane"],
            "status": r["status"],
            "created": time.strftime("%m-%d %H:%M", time.localtime(r["created_at"])),
            "text": _clip(r["text"]),
        })
    return out


def collect(cfg, live):
    notes = []
    try:
        panes = live()
    except Exception as e:  # herdr missing / failed / bad JSON
        log_error(e, "get_live_panes")
        panes = []
        notes.append(f"live pane state unavailable: {type(e).__name__}: {e}")
    try:
        history = read_history(cfg["db_path"], int(cfg["history_limit"]))
    except Exception as e:
        log_error(e, "read_history")
        history = []
        notes.append(f"task history unavailable: {type(e).__name__}: {e}")
    return panes, history, notes


def render(args, cfg, live):
    panes, history, notes = collect(cfg, live)
    if args.json:
        return json.dumps({"panes": panes, "history": history,
                           "total_panes": len(panes), "notes": notes}, indent=2)
    text = format_report(panes, history)
    if notes:
        text += "\n\nNOTES\n" + "\n".join(notes)
    return text


def main(argv=None):
    args = parse_args(argv)
    cfg = load_config()
    live = ttl_cache(float(cfg["cache_ttl"]))(get_live_panes)
    if not args.watch:
        print(render(args, cfg, live))
        return 0
    interval = args.interval if args.interval is not None else float(cfg["interval"])
    try:
        while True:
            out = render(args, cfg, live)
            print("\033[2J\033[H", end="")  # ANSI clear screen, no shell involved
            print(out)
            print(f"\n(refreshing every {interval:g}s, Ctrl+C to stop)")
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
