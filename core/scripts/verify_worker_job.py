# -*- coding: utf-8 -*-
"""Verify a worker job for real (2026-11-05, same night 4 separate rig-worker jobs all claimed success
while lying: 2 did nothing, 1 deleted 94 lines of real code, 1 claimed "all tests pass" with 1 of 3
actually failing). worker_run.py's own doc already said "the boss reads every result" - this makes
that structural instead of relying on remembering to do it by hand each time.

Finds every test_*.py file in the job folder, actually runs it with pytest, and writes a
_verified.json the boss can trust - pass/fail per file, not the worker's own claim. Never trusts
the worker's _worker_log.txt text as evidence of anything; that file is not read here at all.

Usage: python verify_worker_job.py --job <name>   (job folder: C:\\LocalAI\\worker_jobs\\<name>)
Pure core (unit tested): find_test_files(job_dir).
"""
import argparse
import json
import pathlib
import subprocess
import sys
import time

JOBS = pathlib.Path(r"C:\LocalAI\worker_jobs")


def find_test_files(job_dir):
    """[Path, ...] every test_*.py directly in job_dir, sorted. Pure, no execution."""
    job_dir = pathlib.Path(job_dir)
    if not job_dir.is_dir():
        return []
    return sorted(p for p in job_dir.glob("test_*.py") if p.is_file())


def run_one_test_file(path, timeout=120):
    """Runs pytest on one file in its own folder. -> dict with passed bool, returncode, and last 2000 chars of output."""
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", str(path.name)],
                            cwd=str(path.parent), capture_output=True, text=True, timeout=timeout)
        out = (r.stdout or "") + (("\n" + r.stderr) if r.stderr.strip() else "")
        return {"file": path.name, "passed": r.returncode == 0, "returncode": r.returncode, "output_tail": out[-2000:]}
    except subprocess.TimeoutExpired:
        return {"file": path.name, "passed": False, "returncode": None, "output_tail": "TIMED OUT after %ds" % timeout}
    except Exception as e:
        return {"file": path.name, "passed": False, "returncode": None, "output_tail": "ERROR running pytest: %r" % e}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args(argv)

    job_dir = JOBS / a.job
    tests = find_test_files(job_dir)
    results = [run_one_test_file(p, a.timeout) for p in tests]
    all_passed = bool(results) and all(r["passed"] for r in results)

    verdict = {
        "job": a.job,
        "checked_at": time.strftime("%F %T"),
        "test_files_found": len(tests),
        "all_passed": all_passed,
        "results": results,
    }
    if not tests:
        verdict["note"] = "no test_*.py files found in the job folder - this says nothing about whether the actual work is correct, only that there is nothing automated to check"

    out_path = job_dir / "_verified.json"
    out_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")

    print("job:", a.job)
    print("test files found:", len(tests))
    for r in results:
        print(" ", r["file"], "PASS" if r["passed"] else "FAIL")
    print("all_passed:", all_passed)
    print("written to:", out_path)
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
