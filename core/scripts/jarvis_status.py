# -*- coding: utf-8 -*-
"""
Item 91 of the 2026-08-09 tools brainstorm, the natural capstone of the
whole 100-tools project: one command rolling up every digest built across
tonight's batches (security, dev, priorities) plus the dev-focused new
checks from this final batch, instead of running each separately. No new
detection logic of its own - purely a rollup, same "no bloat, consolidate"
principle as security_digest.py/dev_digest.py/priorities_digest.py.

Usage:
    python jarvis_status.py
"""
import sys

sys.path.insert(0, r"J:\Jarvis2\scripts")


def run_status():
    sections = {}

    from security_digest import run_digest as security_run
    sections["Security"] = security_run()

    from dev_digest import run_digest as dev_run
    sections["Dev"] = dev_run()

    from priorities_digest import run_digest as priorities_run
    sections["Priorities"] = priorities_run()

    from path_consistency_check import check_paths
    path_findings = check_paths()
    drift = [label for label, info in path_findings.items() if len(info["variants_found"]) > 1]
    sections["Cross-system"] = [f"Path consistency: {len(drift)} location(s) with multiple forms found"]

    from encoding_guard_lint import check_guards
    missing_guards = check_guards()
    sections["Cross-system"].append(f"Encoding guard lint: {len(missing_guards)} script(s) missing the guard")

    return sections


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("=== Jarvis Status ===\n")
    for section, lines in run_status().items():
        print(f"-- {section} --")
        for line in lines:
            print(f"  {line}")
        print()
