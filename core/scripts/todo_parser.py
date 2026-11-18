#!/usr/bin/env python3
"""todo_parser.py - parse markdown to-do lines and summarize them.

Syntax (one task per line, leading whitespace allowed):
    [x] / [X]   done          [-]  cancelled          [ ]  pending
    optional priority right after the marker: !high  !med  !low
    text = the rest of the line, stripped
Lines that are not tasks, or that carry a malformed !priority, parse to None.

Usage:
    python todo_parser.py FILE.md            text summary
    python todo_parser.py FILE.md --json     JSON (tasks + counts)

Provenance:
  parse_todo_line  hermes-omni's logic. Its pane text had markdown-eaten `*`
                   quantifiers, so the regex below is my restoration, tested.
  summarize / CLI / tests   written by Claude: hermes-nite's CLI piece was
                   never delivered (its reply was an unrelated, wrong
                   get_live_panes answer) and hermes-reliable timed out.
"""
import argparse
import json
import re
import sys

_STATUS_BY_MARKER = {"x": "done", "-": "cancelled", " ": "pending"}
_TODO_RE = re.compile(
    r"^\s*\[(?P<marker>[xX]|-| )\]"
    r"(?:\s*!(?P<priority>high|med|low)(?=\s|$))?"
    r"\s*(?P<text>.*?)\s*$",
    re.IGNORECASE,
)


def parse_todo_line(line):
    """Parse one markdown todo line into a dict, or None if it isn't one.

    Returns {'done': bool, 'status': 'done'|'cancelled'|'pending',
             'priority': 'high'|'med'|'low'|None, 'text': str}
    """
    match = _TODO_RE.match(line)
    if match is None:
        return None

    text = match.group("text")
    # A leading "!word" that isn't high/med/low is a malformed marker.
    if re.match(r"^!\w", text):
        return None

    marker = match.group("marker").lower()
    priority = match.group("priority")
    return {
        "done": marker == "x",
        "status": _STATUS_BY_MARKER[marker],
        "priority": priority.lower() if priority else None,
        "text": text,
    }


def parse_lines(lines):
    return [t for t in (parse_todo_line(l) for l in lines) if t is not None]


def summarize(tasks):
    counts = {"done": 0, "pending": 0, "cancelled": 0}
    by_priority = {"high": 0, "med": 0, "low": 0, "none": 0}
    open_by_priority = {"high": [], "med": [], "low": [], "none": []}
    for t in tasks:
        counts[t["status"]] += 1
        key = t["priority"] or "none"
        by_priority[key] += 1
        if t["status"] == "pending":
            open_by_priority[key].append(t["text"])
    active = counts["done"] + counts["pending"]  # cancelled excluded
    pct = round(100 * counts["done"] / active) if active else 0
    return {"total": len(tasks), "counts": counts, "by_priority": by_priority,
            "open": open_by_priority, "percent_done": pct}


def format_summary(s):
    c = s["counts"]
    out = [f"Tasks: {s['total']}  done {c['done']}  pending {c['pending']}  "
           f"cancelled {c['cancelled']}  ({s['percent_done']}% of non-cancelled done)"]
    for p in ("high", "med", "low", "none"):
        items = s["open"][p]
        if items:
            out.append(f"\nOpen, {p} priority ({len(items)}):")
            out.extend(f"  - {x}" for x in items)
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Summarize a markdown to-do file.")
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    try:
        with open(args.file, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"cannot read {args.file}: {e}", file=sys.stderr)
        return 2
    tasks = parse_lines(lines)
    s = summarize(tasks)
    if args.json:
        print(json.dumps({"tasks": tasks, "summary": s}, indent=2))
    else:
        print(format_summary(s))
        skipped = sum(1 for l in lines if l.strip()) - len(tasks)
        print(f"\n({skipped} non-task lines skipped)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
