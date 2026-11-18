# -*- coding: utf-8 -*-
"""Five-minute tick: the fast jobs that must not wait for the 15-minute sensor runner, in one process (2026-10-30, harness redo, task JarvisLiveness).

Why: 71 scheduled tasks was half the problem. Every fast job used to be its own task with its own python start, its own log and its own way to fail silently.
This runs them in turn, each isolated (one raising must not stop the next) and each logged on one line in logs/tick5.log:
    harness_health   the independent alarm: checks the autonomy invariants, raises intents, wakes the owner only for the safety gate or the machinery at night
    repair_playbook  fixes known failures (a failing sensor, a stale task result, a stale search index) with a tested action and verifies before closing the intent
    job_runner       runs the table of migrated chores in jobs.json (one row each, replacing one scheduled task each); its failures are read by sense_health
    game_auto        switches game mode on/off by itself from the process table (was a side effect inside status_ping.py, so retiring that task would have silently
                     stopped it: found 2026-10-30 while checking what each task to be retired really does)
New fast jobs are added to JOBS with a test; never as a new scheduled task.
    python tick5.py [--only <job>]
Kill switch: logs/tick5_off.txt. Pure core (unit tested): run_jobs(jobs, runner, clock)."""
import importlib
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
JOBS = ("harness_health", "game_auto", "repair_playbook", "job_runner", "proactive_status_gate", "proactive_avatar_trigger", "embed_server_watchdog", "factory_dispatch")


def run_jobs(jobs, runner, clock=time.time):
    """Pure-ish. -> [(name, ok, seconds, note)]. `runner(name)` runs one job and may raise; nothing a job does stops the others."""
    out = []
    for name in jobs:
        t0 = clock()
        try:
            runner(name)
            out.append((name, True, clock() - t0, ""))
        except BaseException as e:               # noqa: BLE001 - SystemExit from a job's main() must not end the tick either
            out.append((name, False, clock() - t0, "%s: %s" % (type(e).__name__, str(e)[:80])))
    return out


def _runner(name):
    mod = importlib.import_module(name)
    code = mod.main([])
    if isinstance(code, int) and code not in (0, None):
        raise RuntimeError("exit code %d" % code)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if os.path.exists(os.path.join(LOGS, "tick5_off.txt")):
        return 0
    jobs = [argv[argv.index("--only") + 1]] if "--only" in argv else list(JOBS)
    res = run_jobs(jobs, _runner)
    with open(os.path.join(LOGS, "tick5.log"), "a", encoding="utf-8") as f:
        for name, ok, secs, note in res:
            f.write("%s %-15s %-4s %5.1fs %s\n" % (time.strftime("%F %T"), name, "ok" if ok else "FAIL", secs, note))
    return 0 if all(r[1] for r in res) else 1


if __name__ == "__main__":
    sys.exit(main())
