# -*- coding: utf-8 -*-
"""Batch launcher for the free OmniRoute workers (2026-10-29: the owner said "use them more").
Runs several narrow jobs in parallel through route_job.py --code-only --run, then checks each result so the boss reads one table.
    python worker_batch.py jobs.json [--max 5]
jobs.json = [{"name": "tj-x", "files": ["J:/Jarvis2/scripts/x.py"], "task": "Write pytest tests for x.py ...", "check": "pytest"}, ...]
  name   unique job folder under C:/LocalAI/worker_jobs (letters, digits, - _)
  files  code files copied into the job folder first (the --code-only data scan refuses emails/ids/secret-looking strings)
  task   the narrow brief ("do not edit the module", exact cases, "run python -m pytest -q")
  check  optional: "pytest" runs python -m pytest -q in the job folder afterwards and reports the tally
Nothing is installed or sent anywhere; results stay in the job folders for the boss to review (re-run tests, mutation-check).
Pure helpers (validate_job, pytest_tally) are unit tested."""
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JOBS = r"C:\LocalAI\worker_jobs"
NAME_OK = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def validate_job(job):
    """Returns a list of problems (empty when the job spec is usable)."""
    bad = []
    if not isinstance(job, dict):
        return ["job is not an object"]
    if not NAME_OK.match(str(job.get("name", ""))):
        bad.append("name must be 1-40 letters, digits, - or _")
    if len(str(job.get("task", "")).strip()) < 40:
        bad.append("task is too short to be a narrow brief (40+ characters)")
    if not isinstance(job.get("files", []), list):
        bad.append("files must be a list")
    if job.get("check") not in (None, "pytest"):
        bad.append("check must be 'pytest' or omitted")
    return bad


def pytest_tally(output):
    """Last 'N passed/failed' line of pytest output, or 'no pytest result'."""
    lines = [l.strip() for l in (output or "").splitlines() if re.search(r"\d+ (passed|failed|error)", l)]
    return lines[-1] if lines else "no pytest result"


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    jobs = json.load(open(argv[0], encoding="utf-8"))
    mx = int(argv[argv.index("--max") + 1]) if "--max" in argv else 5
    names = [j.get("name") for j in jobs if isinstance(j, dict)]
    problems = {j.get("name", "?"): validate_job(j) for j in jobs}
    problems = {k: v for k, v in problems.items() if v}
    if problems or len(set(names)) != len(names):
        print("not started; problems:", problems or "duplicate names")
        return 2
    running, results = [], {}
    pending = list(jobs)
    while pending or running:
        while pending and len(running) < mx:
            j = pending.pop(0)
            d = os.path.join(JOBS, j["name"])
            os.makedirs(d, exist_ok=True)
            for f in j.get("files", []):
                shutil.copy(f, os.path.join(d, os.path.basename(f)))
            p = subprocess.Popen([sys.executable, os.path.join(HERE, "route_job.py"), "--name", j["name"], "--task", j["task"], "--run", "--code-only"],
                                 stdout=open(os.path.join(d, "route_out.txt"), "w"), stderr=subprocess.STDOUT)
            running.append((j, p))
        for item in list(running):
            if item[1].poll() is not None:
                running.remove(item)
                results[item[0]["name"]] = item[1].returncode
        if running:
            import time
            time.sleep(3)
    print("%-22s %-6s %-26s %s" % ("job", "exit", "route", "check"))
    for j in jobs:
        d = os.path.join(JOBS, j["name"])
        route = ""
        try:
            route = [l for l in open(os.path.join(d, "route_out.txt"), encoding="utf-8", errors="ignore") if l.startswith("route:")][0].split("via")[0].strip()[:26]
        except Exception:
            pass
        check = ""
        if j.get("check") == "pytest":
            r = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=d, capture_output=True, text=True)
            check = pytest_tally(r.stdout)
        print("%-22s %-6s %-26s %s" % (j["name"], results.get(j["name"]), route, check))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
