# -*- coding: utf-8 -*-
"""
Item 23 of the 2026-08-09 tools brainstorm: filters Active Priorities.md's
open-task lines down to school-tagged ones, so "what's due for school this
week" is a quick call instead of reading the whole (mixed personal+school)
Active Priorities queue.

Usage:
    from school_digest import school_open_items
    python school_digest.py
"""
import sys
from pathlib import Path

ACTIVE_PRIORITIES_PATH = Path(r"J:\Jarvis-Brain\Active Priorities.md")
SCHOOL_MARKERS = ("[[School]]", "A244", "A245", "A364", "A241", "A232", "A231", "G133", "G135")


def school_open_items():
    if not ACTIVE_PRIORITIES_PATH.exists():
        return []
    lines = ACTIVE_PRIORITIES_PATH.read_text(encoding="utf-8").splitlines()
    items = []
    in_open_section = False
    for line in lines:
        if line.strip() == "### Open Tasks":
            in_open_section = True
            continue
        if line.strip().startswith("### "):
            in_open_section = False
            continue
        if in_open_section and line.strip().startswith(("- [ ]", "  -")) and any(m in line for m in SCHOOL_MARKERS):
            items.append(line.strip())
    return items


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    items = school_open_items()
    print(f"=== {len(items)} open school item(s) ===\n")
    for it in items:
        print(it)
