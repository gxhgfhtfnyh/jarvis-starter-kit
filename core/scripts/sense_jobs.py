# -*- coding: utf-8 -*-
"""Jobs sensor: fourth sensor of the redesigned harness (2026-10-30, step B of the Harness Rebuild Instructions, section 7).

Worker jobs (worker_run.py) write their output into C:/LocalAI/worker_jobs/<name>/, and job_sweep.py adopts the finished tests into scripts/, but only
when someone remembers to run it. On 2026-10-29 finished worker output sat unseen for about ten minutes for that reason. This notices each worker job
whose worker has ended in the last 24 hours (worker_run writes _worker_log.txt when it ends), that has been quiet for two minutes, and that still holds a
test_*.py which is not in scripts/ yet. It raises one tier-1 intent per job so the wake brief surfaces it. Whether to adopt, read or drop it is my judgement
at wake time. It perceives only: it never runs, adopts or deletes a job, and it never sends anything.
    python sense_jobs.py [--dry-run]
Kill switch: logs/sense_jobs_off.txt (--dry-run still runs and writes nothing). Pure core (unit tested): finished(job_dirs, adopted, now), summarize(log_head).
Known limit: a folder reused under the same name while its worker is still running counts as finished once it has been quiet for two minutes, using the old log."""
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import intent_store  # noqa: E402
import job_sweep  # noqa: E402  (its JOBS constant only; importing it runs nothing)

JOBS = job_sweep.JOBS
SCRIPTS = HERE
LOGS = os.path.join(os.path.dirname(HERE), "logs")
OFF = "sense_jobs_off.txt"
LOG_NAME = "_worker_log.txt"
KIND = "job_finished"
VALUE = 3
WINDOW_S = 24 * 3600
QUIET_S = 120
SUMMARY_MAX = 60
HEAD_BYTES = 600


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize(log_head):
    """Pure. At most 60 characters about one worker log: its header ('exit 0, 206s') then the first real line of the report, on one line."""
    lines = [ln.strip() for ln in str(log_head or "").splitlines() if ln.strip()]
    if not lines:
        return "no report text"
    body = ""
    for ln in lines[1:]:
        clean = ln.replace("**", "").replace("`", "").strip(" #>-|")
        if clean:
            body = clean
            break
    text = " ".join(("%s - %s" % (lines[0], body) if body else lines[0]).split())
    return text if len(text) <= SUMMARY_MAX else text[:SUMMARY_MAX - 3].rstrip() + "..."


def finished(job_dirs, adopted, now=None, window_s=WINDOW_S, quiet_s=QUIET_S):
    """Pure. -> [(what, value, dedupe, not_before, deadline)] for each worker job folder that ended in the last window_s seconds, has been quiet for
    quiet_s seconds, and holds a test_*.py whose name is not in adopted (the names already in scripts/). A job ended only when its worker wrote the
    report (the log starts with 'exit'), so a folder still being worked on is not raised. Oldest finish first."""
    now = time.time() if now is None else now
    adopted = set(adopted or ())
    found = []
    for job in job_dirs or []:
        if not isinstance(job, dict) or not job.get("name"):
            continue
        log_m = _num(job.get("log_mtime"))
        head = str(job.get("log_head") or "")
        if log_m is None or not head.lstrip().startswith("exit "):
            continue                              # no report yet: still running, or never finished
        if now - log_m > window_s:
            continue
        files = job.get("files") if isinstance(job.get("files"), dict) else {}
        mtimes = {f: _num(m) for f, m in files.items() if isinstance(f, str)}
        mtimes = {f: m for f, m in mtimes.items() if m is not None}
        others = [m for f, m in mtimes.items() if f != LOG_NAME]
        if others and now - max(others) < quiet_s:
            continue                              # something in the folder was written just now: still being worked on
        if not any(f.startswith("test_") and f.endswith(".py") and f not in adopted for f in mtimes):
            continue                              # nothing left for job_sweep to adopt
        found.append((log_m, str(job["name"]), head))
    out = []
    for log_m, name, head in sorted(found):
        out.append(("%s: %s" % (name, summarize(head)), VALUE, "job-%s-%d" % (name, int(log_m)), None, None))
    return out


def read_jobs(jobs_root):
    """Read-only. -> [{name, files: {file: mtime}, log_mtime, log_head}] for each folder under jobs_root (top-level files only)."""
    out = []
    try:
        names = sorted(os.listdir(jobs_root))
    except OSError:
        return out
    for name in names:
        folder = os.path.join(jobs_root, name)
        if not os.path.isdir(folder):
            continue
        files = {}
        try:
            for f in os.listdir(folder):
                p = os.path.join(folder, f)
                if os.path.isfile(p):
                    files[f] = os.path.getmtime(p)
        except OSError:
            continue
        log_head = ""
        if LOG_NAME in files:
            try:
                with open(os.path.join(folder, LOG_NAME), encoding="utf-8", errors="replace") as fh:
                    log_head = fh.read(HEAD_BYTES)
            except OSError:
                log_head = ""
        out.append({"name": name, "files": files, "log_mtime": files.get(LOG_NAME), "log_head": log_head})
    return out


def adopted_names(scripts_dir):
    """Read-only. The file names already in scripts/. job_sweep counts a worker test as adopted when scripts/<same name> exists."""
    try:
        return set(os.listdir(scripts_dir))
    except OSError:
        return set()


def main(argv=None):
    argv = argv or []
    dry = "--dry-run" in argv
    if not dry and os.path.exists(os.path.join(LOGS, OFF)):
        return 0
    now = time.time()
    found = finished(read_jobs(JOBS), adopted_names(SCRIPTS), now=now)
    intents = intent_store.read_jsonl(intent_store.INTENTS, LOGS)
    fresh = [f for f in found if not intent_store.is_duplicate(intents, {"dedupe": f[2]}, now=now, cooldown=WINDOW_S)]
    if dry:
        for what, value, _dedupe, _nb, _dl in fresh:
            print("would raise v%d %s %s" % (value, KIND, what))
        print("%d finding(s), %d would be new, nothing written" % (len(found), len(fresh)))
        return 0
    for what, value, dedupe, nb, dl in fresh:
        intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(now), "source": "jobs", "kind": KIND, "data": {"what": what, "value": value}}, LOGS)
        intents.append(intent_store.new_intent(KIND, what, tier=1, value=value, dedupe=dedupe, deadline=dl, from_event=KIND, ts=now, not_before=nb))
    if fresh:
        intent_store.write_intents(intents, LOGS)
    print("sense_jobs: %d finding(s), %d new intent(s)" % (len(found), len(fresh)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
