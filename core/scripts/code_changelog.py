# -*- coding: utf-8 -*-
"""
Item 76 of the 2026-08-09 tools brainstorm: "what changed in the code
recently" from real git history - the code-side counterpart to batch 4's
vault_recent_activity.py (which covers vault notes, not scripts). Filters
out the routine auto "snapshot: <timestamp>" commits scripts_snapshot.ps1
makes on every Stop hook, since those aren't meaningful changelog entries.

Usage:
    from code_changelog import recent_changes
    python code_changelog.py [--days N]
"""
import subprocess
import sys

SCRIPTS_DIR = r"J:\Jarvis2\scripts"
DEFAULT_DAYS = 1


def recent_changes(days=DEFAULT_DAYS):
    result = subprocess.run(
        ["git", "-C", SCRIPTS_DIR, "log", f"--since={days}.days", "--oneline"],
        capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    lines = result.stdout.strip().splitlines()
    return [l for l in lines if not l.split(" ", 1)[-1].startswith("snapshot:")]


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    days = DEFAULT_DAYS
    if "--days" in sys.argv:
        days = int(sys.argv[sys.argv.index("--days") + 1])
    changes = recent_changes(days)
    print(f"=== {len(changes)} meaningful commit(s) in the last {days} day(s) ===\n")
    for c in changes:
        print(c)
