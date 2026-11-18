# -*- coding: utf-8 -*-
"""The "software factory" pattern (2026-11-12, from the two IndyDevDan videos the owner sent earlier
tonight, parked at focus.md:441 - cron-triggered agents that keep building things unsupervised) applied
to infrastructure we already have, instead of building something new from scratch: worker_run.py already
wraps the local/rig model in full Claude Code (real file read/write, running tests), so the missing piece
was only the recurring dispatch loop, not a new harness. His exact ask: "if we can set things up like them
and give it to a local model we might be able to get something thats actually competent enough to help u
with work... for u to use him not me" -- this is that dispatch loop, one well-scoped backlog item at a
time, never more than one job running at once.

2026-11-12, same night, his direct push: "alright lets build its harness like what all the vids i sent
u" -- tried making pi_worker_run.py (Pi, not Claude Code) the new default engine here, since Pi has no
~30k-token system-prompt overhead and was live-verified end to end against the real local-qwen provider.
A real automated security review caught a genuine gap minutes later: Pi has no per-command bash pattern
allow/deny list the way Claude Code does (worker_run.py explicitly denies Bash(curl*)/Bash(rm*)/
Bash(powershell*)/Bash(cmd*) etc; Pi can only switch a tool fully on or off), so pi_worker_run.py's
--no-approve plus a fully-open bash tool is a real, meaningfully weaker boundary for this unattended,
every-5-min, no-human-in-the-loop dispatch loop specifically -- not for a one-off run I watch myself.
Reverted the DEFAULT engine back to "claude" (worker_run.py, the fine-grained allowlisted path) for this
automated loop; a backlog item can still opt into "engine": "pi" deliberately, but that is no longer
the unattended default until pi_worker_run.py gets a real sandbox or an equivalent command filter.

Tick5 job (every 5 min). A tiny FIFO backlog (logs/factory_backlog.json, a list of {name, task, backend}) --
deliberately NOT auto-populated from Active Priorities.md (117 real open items he cares about; silently
pulling from there risks exactly the thing flagged earlier tonight as too risky to touch unsupervised).
I add real, well-scoped, vetted tasks to it by hand. Each tick:
  - if a job is already running: check whether it finished (worker_run.py's own _worker_log.txt exists in
    its job folder) or went stale (no result after STALE_AFTER_S, well past worker_run's own 600s default
    timeout) -- either way, clear the slot and raise a review intent (I, the boss, read every result
    myself per worker_run.py's own existing rule, nothing here auto-merges anything).
  - else if the backlog has something and a backend is actually up: launch worker_run.py DETACHED (never
    block tick5's own loop on a job that can take up to 10+ minutes) and record it as the running job.
Kill switch: logs/factory_dispatch_off.txt.
Pure core (unit tested): should_dispatch(current_job, backlog), is_job_done(worker_log_path),
is_job_stale(started_epoch, now, stale_after_s)."""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
BACKLOG = os.path.join(LOGS, "factory_backlog.json")
STATE = os.path.join(LOGS, "factory_state.json")
JOBS_DIR = r"C:\LocalAI\worker_jobs"
STALE_AFTER_S = 1500  # 25 min: well past worker_run.py's own 600s default timeout, generous buffer


def should_dispatch(current_job, backlog):
    """Pure. True when nothing is running and there's real backlog work to start."""
    return current_job is None and bool(backlog)


def is_job_done(worker_log_path):
    """Pure (given a path-exists check injected as os.path.exists in the real caller)."""
    return os.path.exists(worker_log_path)


def is_job_stale(started_epoch, now, stale_after_s=STALE_AFTER_S):
    """Pure. True when a dispatched job has had more than stale_after_s with no result."""
    return (now - started_epoch) > stale_after_s


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


def _raise_review_intent(name, reason):
    import intent_store

    what = "Factory worker job '%s' %s. Read C:\\LocalAI\\worker_jobs\\%s\\_worker_log.txt yourself and run its tests before trusting anything in it (never merge on the worker's own claim)." % (name, reason, name)
    cand = intent_store.new_intent("factory_job_ready", what, tier=1, value=3, dedupe="factory-%s" % name, from_event="factory_dispatch")
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    if not intent_store.is_duplicate(intents, cand):
        intents.append(cand)
        intent_store.write_intents(intents)


def _launch(name, task, backend, engine="claude"):
    """engine: "pi" (default, pi_worker_run.py - lighter, no Claude Code system-prompt overhead, proven
    2026-11-12 against local-qwen) or "claude" (worker_run.py, for a backend with no models.json provider
    yet, or a task that genuinely needs something Pi's tool set doesn't cover)."""
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS -- never block tick5 on a 10-min job
    script = "pi_worker_run.py" if engine == "pi" else "worker_run.py"
    subprocess.Popen([sys.executable, os.path.join(HERE, script), "--name", name, "--task", task, "--backend", backend],
                      creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None):
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(os.path.join(LOGS, "factory_dispatch_off.txt")):
            return
        state = _read_json(STATE, {"current": None, "started": 0})
        now = time.time()
        current = state.get("current")
        if current:
            log_path = os.path.join(JOBS_DIR, current, "_worker_log.txt")
            if is_job_done(log_path):
                _raise_review_intent(current, "finished")
                state["current"] = None
            elif is_job_stale(state.get("started", 0), now):
                _raise_review_intent(current, "has not produced a result in over 25 min (stale/stuck)")
                state["current"] = None
        backlog = _read_json(BACKLOG, [])
        if should_dispatch(state.get("current"), backlog):
            item = backlog.pop(0)
            _launch(item["name"], item["task"], item.get("backend", "auto"), item.get("engine", "claude"))
            state["current"] = item["name"]
            state["started"] = now
            _write_json(BACKLOG, backlog)
        _write_json(STATE, state)
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a dispatch bug
        return


if __name__ == "__main__":
    main()
