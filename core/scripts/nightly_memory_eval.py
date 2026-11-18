# -*- coding: utf-8 -*-
"""Nightly memory eval (2026-10-30, step F4 of the harness redo): runs all four recall sets through the command I actually use for recall (msearch.py) so a
drop in memory quality is found the night it happens, not when an answer comes out wrong.
Why: the sets (main 20, fresh 16, held-out 10, stale-fact 8) were run by hand when someone remembered. memory_eval.py appends one summary row per set to
logs/memory_eval.jsonl; sense_self already reads those rows and raises an intent when a set falls two or more answers below its best.
    python nightly_memory_eval.py [--dry-run]        # --dry-run prints the four commands and runs nothing
Run daily by task JarvisMemoryEval. Takes about 1 to 3 minutes (one second per question). Pure core: build_cmds(py, tool)."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETS = ("", "--fresh", "--fresh2", "--stale")


def build_cmds(py=None, tool=None):
    """Pure. One command per recall set."""
    py = py or sys.executable
    tool = tool or os.path.join(HERE, "msearch.py")
    return [[py, os.path.join(HERE, "memory_eval.py"), "--tool", tool] + ([flag] if flag else []) for flag in SETS]


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    cmds = build_cmds()
    if "--dry-run" in argv:
        for c in cmds:
            print(" ".join(c))
        return 0
    bad = 0
    for c in cmds:
        t0 = time.time()
        r = subprocess.run(c, capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
        tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        print("%s | %s | %ds" % (c[-1] if c[-1].startswith("--") and c[-1] != c[3] else "main", tail[:90], time.time() - t0))
        bad += 1 if r.returncode not in (0, 1) else 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
