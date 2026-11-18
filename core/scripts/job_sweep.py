# -*- coding: utf-8 -*-
"""Sweep finished worker jobs and adopt their tests (2026-10-29, the owner: "you were idle until i said ... then you realized the files").
Cause: worker_batch.py only notifies when the WHOLE batch ends, so finished test files sat in C:\\LocalAI\\worker_jobs unseen for ~10 minutes.
    python job_sweep.py            # list finished worker tests that are not in scripts/ yet
    python job_sweep.py --adopt    # also adopt each one with adopt_worker_tests.py --drop-failing (scan, doctor, run, drop failing)
A job folder counts when it holds test_<x>.py whose module <x>.py is also in the folder, scripts/test_<x>.py does not exist yet, and the file has not been
touched in the last 60 seconds (the worker may still be writing). Pure core (unit tested): pending(jobs_root, scripts_dir, now, quiet_s)."""
import os
import subprocess
import sys
import time

JOBS = r"C:\LocalAI\worker_jobs"
SCRIPTS = r"J:\Jarvis2\scripts"


def pending(jobs_root=JOBS, scripts_dir=SCRIPTS, now=None, quiet_s=60):
    """[(job_dir, test_file)] of finished worker tests that are not adopted yet."""
    now = time.time() if now is None else now
    out = []
    try:
        names = sorted(os.listdir(jobs_root))
    except OSError:
        return out
    for d in names:
        folder = os.path.join(jobs_root, d)
        if not os.path.isdir(folder):
            continue
        for f in sorted(os.listdir(folder)):
            if not (f.startswith("test_") and f.endswith(".py")):
                continue
            module = f[len("test_"):]
            if not os.path.exists(os.path.join(folder, module)) or not os.path.exists(os.path.join(scripts_dir, module)) or os.path.exists(os.path.join(scripts_dir, f)):      # only tests for live scripts that exist here
                continue
            if now - os.path.getmtime(os.path.join(folder, f)) < quiet_s:
                continue
            out.append((folder, f))
    return out


def main(argv):
    todo = pending()
    for folder, f in todo:
        print("finished worker test not adopted: %s in %s" % (f, os.path.basename(folder)))
        if "--adopt" in argv:
            r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "adopt_worker_tests.py"), folder, f, f, "--drop-failing"], capture_output=True, text=True, timeout=400)
            print("   " + ((r.stdout.strip().splitlines() or ["(no output)"])[-1])[:200])
    if not todo:
        print("no finished worker tests waiting")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
