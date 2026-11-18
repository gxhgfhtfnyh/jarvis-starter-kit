# -*- coding: utf-8 -*-
"""Turn scheduled tasks into job-runner rows, and migrate them one at a time with an automatic way back (2026-10-30, harness redo; cutover of the 67 running tasks).
    python export_jobs.py                      # list every task: what row it would become, or why it cannot be migrated (reads only)
    python export_jobs.py --migrate "<task name>" [--allow-sends] [--argv script.py args...]   # add its row to jobs.json, export its XML, run the row once, disable the task only if the run was clean (--argv last: use that command instead of the one parsed from the task)
    python export_jobs.py --rollback "<task name>"                  # re-enable the task, and only once it is enabled remove the row
Only tasks that are safe to move are offered: enabled, run level Limited, a python or PowerShell script in this folder, on a plain time, daily or weekly trigger (not at logon,
not on an event, not "every 2 weeks", not a repetition that stops after a few hours). Tasks that send messages to people are refused unless --allow-sends: a forced first run
would send one early. Every migration exports the task XML to backups/ first. The whole table edit runs under a lock, a table that cannot be parsed is never overwritten, and
any failure after the row is saved removes the row again (and re-enables the task if it was already disabled), so a task never ends up running twice or not at all.
Pure core (unit tested): task_to_job(task), parse_action(execute, arguments), day_from_mask(mask), popcount(mask)."""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
LOGS_DIR = os.environ.get("JARVIS_LOGS") or os.path.join(BASE, "logs")
JOBS_FILE = os.path.join(HERE, "jobs.json")
BACKUPS = os.path.join(BASE, "backups")
SENDERS = ("digest", "mail", "briefing", "status", "presence", "reminder", "alert", "daily_code", "debrief", "lab_report", "scoreboard", "fallback", "contact_health", "response",
           "school", "self_test", "proactive", "calendar_mood", "gmail", "keepsake", "room_security", "autopilot", "sensor", "morning")
LOOP_ITSELF = ("tick5.py", "job_runner.py", "harness_health.py", "sensor_runner.py", "autopilot.py", "export_jobs.py")
MAX_TIMEOUT = 300
PS = ("Get-ScheduledTask -TaskName Jarvis* | ForEach-Object { $t = $_; $i = Get-ScheduledTaskInfo -TaskName $t.TaskName; [pscustomobject]@{ Name=$t.TaskName; State=[string]$t.State; "
      "Level=[string]$t.Principal.RunLevel; Last=$i.LastTaskResult; Limit=[string]$t.Settings.ExecutionTimeLimit; Triggers=@($t.Triggers | ForEach-Object { [pscustomobject]@{ Type=$_.CimClass.CimClassName; "
      "Start=[string]$_.StartBoundary; Interval=[string]$_.Repetition.Interval; Duration=[string]$_.Repetition.Duration; DaysOfWeek=$_.DaysOfWeek; DaysInterval=$_.DaysInterval; "
      "WeeksInterval=$_.WeeksInterval } }); Actions=@($t.Actions | ForEach-Object { [pscustomobject]@{ Exec=$_.Execute; Args=$_.Arguments } }) } } | ConvertTo-Json -Depth 6")


class TableUnreadable(Exception):
    pass


def popcount(mask):
    try:
        return bin(int(mask)).count("1")
    except (TypeError, ValueError):
        return 0


def day_from_mask(mask):
    """Pure. Task Scheduler DaysOfWeek bitmask (Sunday=1, Monday=2 ... Saturday=64) -> Python weekday of the first set day (Monday=0), or None."""
    try:
        m = int(mask)
    except (TypeError, ValueError):
        return None
    for bit, wd in ((2, 0), (4, 1), (8, 2), (16, 3), (32, 4), (64, 5), (1, 6)):
        if m & bit:
            return wd
    return None


def _args(rest):
    """Split an argument string the way a command line does, without keeping the quote characters (review finding 9)."""
    return [a or b for a, b in re.findall(r'"([^"]*)"|(\S+)', rest)] if rest else []


def parse_action(execute, arguments):
    """Pure. -> (script file name, [args]) when the action runs a python script or a .ps1 that lives in the scripts folder, else (None, reason)."""
    ex = os.path.basename(str(execute or "").strip().strip('"').replace("\\", "/")).lower()
    args = str(arguments or "").strip()
    if ex in ("pythonw.exe", "python.exe", "py.exe"):
        m = re.match(r'^"?([^"]*?[\\/])?([A-Za-z0-9_\-]+\.py)"?\s*(.*)$', args)
    elif ex in ("powershell.exe", "pwsh.exe"):
        m = re.search(r'-File\s+"?([^"]*?[\\/])?([A-Za-z0-9_\-]+\.ps1)"?\s*(.*)$', args)
    elif ex == "wscript.exe" and "run_hidden.vbs" in args.lower():          # the hidden-window wrapper: the real script is the argument after it
        m = re.search(r'run_hidden\.vbs"?\s+"?([^"]*?[\\/])?([A-Za-z0-9_\-]+\.(?:ps1|py))"?\s*(.*)$', args, re.I)
        if m and m.group(2).lower().endswith(".py"):
            return None, "a python script behind the hidden-window wrapper (not seen yet, left alone)"
    else:
        return None, "runs %s, not a python or PowerShell script" % (ex or "nothing")
    if not m:
        return None, "could not read the script from its arguments"
    folder = (m.group(1) or "").lower().replace("\\", "/")
    if folder and "jarvis2/scripts" not in folder:
        return None, "script is outside the scripts folder"
    return m.group(2), _args(m.group(3).strip())


def script_exists(name):
    return os.path.exists(os.path.join(HERE, name))


def _hhmm(start):
    m = re.search(r"T(\d\d):(\d\d)", str(start or ""))
    return "%s:%s" % (m.group(1), m.group(2)) if m else None


def _minutes(iso_interval):
    m = re.match(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$", str(iso_interval or ""))
    if not m or not any(m.groups()):
        return None
    return int(m.group(1) or 0) * 1440 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) + (int(m.group(4) or 0) + 59) // 60


def _timeout_s(limit):
    """Pure. The task's own execution limit when it is shorter than the runner's cap, else the cap."""
    mins = _minutes(limit)
    return int(min(MAX_TIMEOUT, max(60, mins * 60))) if mins else MAX_TIMEOUT


def task_to_job(t, allow_sends=False):
    """Pure. t: one task as read from PowerShell. -> (job dict or None, reason)."""
    if str(t.get("State", "")).lower() != "ready":
        return None, "state is %s" % t.get("State")
    if str(t.get("Level", "")).lower() != "limited":
        return None, "run level %s (it cannot see J: and needs an admin window to change)" % t.get("Level")
    acts, trigs = t.get("Actions") or [], t.get("Triggers") or []
    if len(acts) != 1 or len(trigs) != 1:
        return None, "%d actions and %d triggers (only one of each is migrated)" % (len(acts), len(trigs))
    script, args = parse_action(acts[0].get("Exec"), acts[0].get("Args"))
    if script is None:
        return None, args
    if not script_exists(script):
        return None, "script %s is not in the scripts folder" % script
    tr = trigs[0]
    ty = str(tr.get("Type", ""))
    if not any(k in ty for k in ("TimeTrigger", "DailyTrigger", "WeeklyTrigger")):
        return None, "trigger %s is not a plain time, daily or weekly trigger (logon, boot, event, idle and monthly tasks stay as they are)" % (ty or "unknown")
    job = {"id": re.sub(r"[^a-z0-9]+", "_", t["Name"].lower()).strip("_"), "argv": [script] + args, "timeout_s": _timeout_s(t.get("Limit")), "source_task": t["Name"]}
    interval = _minutes(tr.get("Interval"))
    duration = _minutes(tr.get("Duration"))
    if interval and duration is not None and duration < 1440:
        return None, "it repeats only for %d minutes after its start time (the job table has no such window)" % duration
    try:
        if int(tr.get("DaysInterval") or 1) > 1:
            return None, "it runs every %s days (the job table runs daily)" % tr.get("DaysInterval")
        if int(tr.get("WeeksInterval") or 1) > 1:
            return None, "it runs every %s weeks (the job table runs weekly)" % tr.get("WeeksInterval")
    except (TypeError, ValueError):
        pass
    if "WeeklyTrigger" in ty:
        if popcount(tr.get("DaysOfWeek")) > 1:
            return None, "it runs on several days of the week (the job table takes one day)"
        wd = day_from_mask(tr.get("DaysOfWeek"))
        at = _hhmm(tr.get("Start"))
        if wd is None or not at:
            return None, "weekly trigger without a readable day or time"
        job["weekly"] = {"day": wd, "at": at}
    elif interval:
        job["every_min"] = interval
    elif "DailyTrigger" in ty:
        at = _hhmm(tr.get("Start"))
        if not at:
            return None, "daily trigger without a readable time"
        job["daily"] = at
    else:
        return None, "trigger type %s without a repeat interval (a one-shot task)" % ty
    low = (t["Name"] + " " + script).lower()
    if script in LOOP_ITSELF:
        return None, "part of the loop itself: it keeps its own task, so a broken runner cannot take its own watcher down with it"
    if not allow_sends and any(s in low for s in SENDERS):
        return None, "may send a message or is part of the harness's own machinery (a forced first run would send one early): use --allow-sends"
    return job, "ok"


def read_tasks():
    r = subprocess.run(["powershell", "-NoProfile", "-Command", PS], capture_output=True, text=True, timeout=180, encoding="utf-8", errors="replace")
    data = json.loads(r.stdout or "[]")
    return data if isinstance(data, list) else [data]


def _load_jobs():
    """The table. A missing file is an empty table; a file that exists but cannot be parsed or read raises, so it is never overwritten with a one-row table."""
    try:
        with open(JOBS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        raise TableUnreadable("jobs.json cannot be read (%s); fix or restore it first, nothing was changed" % type(e).__name__)
    if not isinstance(data, list):
        raise TableUnreadable("jobs.json is not a list; fix or restore it first, nothing was changed")
    return data


def _save_jobs(jobs):
    tmp = JOBS_FILE + ".tmp.%d" % os.getpid()
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=1, ensure_ascii=False)
    for attempt in range(5):
        try:
            os.replace(tmp, JOBS_FILE)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def _ps(cmd):
    return subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=120, encoding="utf-8", errors="replace")


def _q(name):
    return name.replace("'", "''")


class _Undo(Exception):
    pass


def _remove_row(job_id):
    try:
        _save_jobs([j for j in _load_jobs() if j.get("id") != job_id])
    except TableUnreadable:
        pass


def migrate(name, allow_sends=False, argv=None):
    """-> (ok, message). Adds the row, backs up the task, runs the row once, disables the task only if that run was clean; any failure afterwards removes the row (and
    re-enables the task if it had been disabled). argv replaces the parsed command: for a task whose wrapper only forwards to a python script and redirects its output to a log."""
    import loop_lock
    try:
        with loop_lock.locked("jobs_table", "export_jobs-%d" % os.getpid(), ttl=900, wait=120, logs=LOGS_DIR):
            return _migrate(name, allow_sends, argv)
    except loop_lock.LockHeld:
        return False, "another migration is changing the job table right now; try again in a minute"
    except TableUnreadable as e:
        return False, str(e)


def _migrate(name, allow_sends, argv):
    tasks = {t["Name"]: t for t in read_tasks()}
    if name not in tasks:
        return False, "no such task"
    job, why = task_to_job(tasks[name], allow_sends)
    if job is None:
        return False, "not migrated: " + why
    if argv:
        if not script_exists(argv[0]):
            return False, "script %s is not in the scripts folder" % argv[0]
        job["argv"] = list(argv)
    jobs = _load_jobs()
    if any(j.get("id") == job["id"] for j in jobs):
        return False, "already in jobs.json"
    folder = os.path.join(BACKUPS, "tasks_" + time.strftime("%Y-%m-%d") + "_migrated")
    os.makedirs(folder, exist_ok=True)
    xml = os.path.join(folder, name.replace(" ", "_") + ".xml")
    _ps("Export-ScheduledTask -TaskName '%s' | Out-File -Encoding utf8 '%s'" % (_q(name), xml.replace("'", "''")))
    if not os.path.exists(xml):
        return False, "could not export the task XML first, so nothing was changed"
    jobs.append(job)
    _save_jobs(jobs)
    touched = False                                           # True once the Disable call may have taken effect, even if it then raised or timed out
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "job_runner.py"), "--only", job["id"], "--force"], capture_output=True, text=True,
                           timeout=job.get("timeout_s", MAX_TIMEOUT) + 150, encoding="utf-8", errors="replace")
        last = ((r.stdout or r.stderr).strip().splitlines() or ["?"])[-1][:80]
        if r.returncode != 0:
            raise _Undo("the first run of the row did not run clean (exit %d: %s)" % (r.returncode, last))
        touched = True
        d = _ps("Disable-ScheduledTask -TaskName '%s' | Out-Null; (Get-ScheduledTask -TaskName '%s').State" % (_q(name), _q(name)))
        if "Disabled" not in (d.stdout or ""):
            raise _Undo("could not confirm the task is disabled (%s)" % ((d.stderr or d.stdout).strip()[:60]))
        return True, "migrated: job %s, task disabled, XML in %s" % (job["id"], folder)
    except _Undo as e:
        reason = str(e)
    except Exception as e:                                    # noqa: BLE001 - a timeout or any other surprise after the row was saved
        reason = "%s after the row was saved" % type(e).__name__
    _remove_row(job["id"])
    if touched:                                               # the task was Ready when this began, so enabling it again is always right; then check
        try:
            d = _ps("Enable-ScheduledTask -TaskName '%s' | Out-Null; (Get-ScheduledTask -TaskName '%s').State" % (_q(name), _q(name)))
            state = (d.stdout or "?").strip()
        except Exception as e:                                # noqa: BLE001
            state = type(e).__name__
        if state not in ("Ready", "Running"):
            return False, "%s; the row was removed but the task could not be re-enabled (state %s): enable it by hand" % (reason, state)
    return False, "%s, so the row was removed and the task left as it was" % reason


def rollback(name):
    """Re-enable the task first (that needs nothing but its name); the row is removed only once the task is confirmed enabled and the table can be read, so at every moment
    at least one of the two will run."""
    import loop_lock
    try:
        with loop_lock.locked("jobs_table", "export_jobs-%d" % os.getpid(), ttl=900, wait=120, logs=LOGS_DIR):
            d = _ps("Enable-ScheduledTask -TaskName '%s' | Out-Null; (Get-ScheduledTask -TaskName '%s').State" % (_q(name), _q(name)))
            state = (d.stdout or "?").strip()
            if state not in ("Ready", "Running"):
                return False, "could not re-enable the task (state %s), so the row was kept and the job keeps running from the table" % state
            try:
                jobs = _load_jobs()
            except TableUnreadable as e:
                return True, "the task is enabled again (state %s), but its row is still in jobs.json because the table cannot be read: %s" % (state, e)
            keep = [j for j in jobs if j.get("source_task") != name]
            _save_jobs(keep)
            return True, "removed %d row(s); task state: %s" % (len(jobs) - len(keep), state)
    except loop_lock.LockHeld:
        return False, "another migration is changing the job table right now; try again in a minute"


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if "--migrate" in argv:
        over = argv[argv.index("--argv") + 1:] if "--argv" in argv else None
        ok, msg = migrate(argv[argv.index("--migrate") + 1], "--allow-sends" in argv, over)
        print(("OK  " if ok else "NO  ") + msg)
        return 0 if ok else 1
    if "--rollback" in argv:
        ok, msg = rollback(argv[argv.index("--rollback") + 1])
        print(("OK  " if ok else "NO  ") + msg)
        return 0 if ok else 1
    n_ok = 0
    for t in sorted(read_tasks(), key=lambda x: x["Name"]):
        job, why = task_to_job(t, allow_sends="--allow-sends" in argv)
        if job:
            n_ok += 1
            sched = ("every %d min" % job["every_min"]) if "every_min" in job else ("daily %s" % job["daily"]) if "daily" in job else "weekly day %d %s" % (job["weekly"]["day"], job["weekly"]["at"])
            print("MIGRATABLE  %-36s %-14s %s" % (t["Name"], sched, " ".join(job["argv"])))
        else:
            print("no          %-36s %s" % (t["Name"], why))
    print("%d task(s) can be migrated" % n_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
