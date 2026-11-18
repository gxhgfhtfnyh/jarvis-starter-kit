# -*- coding: utf-8 -*-
"""
Item 80 of the 2026-08-09 tools brainstorm: same "one call instead of five"
aggregation as batch 7's security_digest.py, but for dev/deployment health -
compile status, cold-start self-test, undocumented/orphaned scripts (via
dead_script_check.py), and recent real code changes, all in one pass.

Usage:
    python dev_digest.py
"""
import sys


def run_digest():
    lines = []

    from compile_check_all import check_all
    failures = check_all()
    lines.append(f"Compile check: {len(failures)} failure(s)")

    from dead_script_check import check as dead_script_run
    undocumented = [f for f in dead_script_run() if not f["documented"]]
    lines.append(f"Undocumented/possibly-orphaned scripts: {len(undocumented)}")

    from code_changelog import recent_changes
    changes = recent_changes(days=1)
    lines.append(f"Real code commits (last 1 day): {len(changes)}")

    from cold_start_selftest import run as cold_start_run
    results = cold_start_run()
    issue_count = (0 if results["course_count"]["ok"] else 1)
    for key in ("claude_md_broken_links", "vault_index_broken_links", "claude_md_missing_paths", "vault_index_missing_paths"):
        issue_count += len(results[key])
    lines.append(f"Cold-start self-test: {issue_count} issue(s)")

    return lines


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== Dev Digest ===\n")
    for line in run_digest():
        print(line)
