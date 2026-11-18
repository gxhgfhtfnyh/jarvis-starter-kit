# -*- coding: utf-8 -*-
"""
Item 74 of the 2026-08-09 tools brainstorm: formalizes the manual
"python -m py_compile <file>" step already run by hand after every single
script tonight into one real batch check across the whole scripts folder -
catches a syntax error in a script that ISN'T the one just edited (e.g. a
shared import broke something else).

Usage:
    from compile_check_all import check_all
    python compile_check_all.py
"""
import py_compile
import sys
from pathlib import Path

SCRIPTS_DIR = Path(r"J:\Jarvis2\scripts")


def check_all():
    failures = []
    for p in SCRIPTS_DIR.glob("*.py"):
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as e:
            failures.append((str(p.relative_to(SCRIPTS_DIR)), str(e)))
    return failures


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    failures = check_all()
    total = len(list(SCRIPTS_DIR.glob("*.py")))
    print(f"=== {total} script(s) checked, {len(failures)} failure(s) ===\n")
    for name, err in failures:
        print(f"[FAIL] {name}\n  {err}\n")
