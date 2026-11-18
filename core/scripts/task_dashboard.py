# -*- coding: utf-8 -*-
"""
At-a-glance health check across every registered "Jarvis *" Scheduled Task -
state, last run time/result, next run time, straight from Windows Task
Scheduler (works for every task regardless of whether it logs through
jarvis_log.py), plus jarvis_log detail for jobs that do.

Usage:
    python task_dashboard.py
"""
import subprocess
import sys

sys.path.insert(0, r"J:\Jarvis2\scripts")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Task Scheduler name -> jarvis_log.py job name, for tasks that actually log
# through it. Tasks not listed here (Hotkey Listener runs continuously, not
# on a schedule; Auto-Lock/Scratch Cleanup/Vault Backup don't use jarvis_log
# yet) just show their native Task Scheduler status with no log detail.
LOG_JOB_MAP = {
    "Jarvis Morning Inbox Check": "morning_inbox_check",
}

PS_QUERY = r"""
function Fmt($d) { if ($d -and $d.Year -gt 1900) { $d.ToString("yyyy-MM-dd HH:mm:ss") } else { "never" } }
Get-ScheduledTask | Where-Object { $_.TaskName -like "*Jarvis*" } | ForEach-Object {
    $info = Get-ScheduledTaskInfo -TaskName $_.TaskName
    [PSCustomObject]@{
        Name = $_.TaskName
        State = $_.State.ToString()
        LastRunTime = Fmt $info.LastRunTime
        LastTaskResult = $info.LastTaskResult
        NextRunTime = Fmt $info.NextRunTime
    } | ConvertTo-Json -Compress
}
"""

# Common Task Scheduler result codes worth a friendly label instead of a raw
# decimal - 0 means success, everything else is either a real failure or a
# benign "hasn't happened yet" state.
RESULT_CODES = {
    0: "OK",
    267011: "never run",
    267009: "currently running",
}


def describe_result(code):
    return RESULT_CODES.get(code, f"CODE {code}")


def get_task_status():
    # Real bug found 2026-08-11 (the owner flagged a recurring flashing
    # PowerShell window): same root cause already documented for the
    # nvidia-smi call in avatar_context_check.py - a console app spawned
    # from a windowless parent (pythonw.exe, here via the "Jarvis Avatar
    # Context Check" task that runs every ~15 minutes) still gets its own
    # new visible console on Windows unless explicitly suppressed.
    # CREATE_NO_WINDOW fixes it at the source, same as the earlier fix.
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", PS_QUERY],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"schtasks query failed: {result.stderr[:300]}")
    import json
    tasks = []
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if line:
            tasks.append(json.loads(line))
    return tasks


def print_dashboard():
    tasks = get_task_status()
    if not tasks:
        print("No Jarvis scheduled tasks found.")
        return

    from jarvis_log import summarize

    print("=== Jarvis Scheduled Task Dashboard ===\n")
    for t in sorted(tasks, key=lambda x: x["Name"]):
        result_flag = describe_result(t["LastTaskResult"])
        print(f"[{t['State']:>7}] {t['Name']}")
        print(f"          Last run: {t['LastRunTime']}  ({result_flag})")
        print(f"          Next run: {t['NextRunTime']}")
        job_name = LOG_JOB_MAP.get(t["Name"])
        if job_name:
            print(f"          Log: {summarize(job_name)}")
        print()


if __name__ == "__main__":
    print_dashboard()
