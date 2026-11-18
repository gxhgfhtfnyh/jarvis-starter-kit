"""
Structured (JSON-lines) logging for recurring/scheduled Jarvis scripts, replacing
plain-text .log files that can only be grepped, not queried. One JSON object per
line - easy to load with pandas/jq/plain json.loads() per line, filter by date range,
count outcomes, etc.

Usage:
    from jarvis_log import log_run

    log_run("morning_inbox_check", success=True, detail="0 unread, nothing flagged")
    log_run("morning_inbox_check", success=False, detail="Outlook profile locked after 3 retries")
"""
import json
import datetime as dt
from pathlib import Path

LOG_DIR = Path(r"J:\Jarvis2\logs")
LOG_DIR.mkdir(exist_ok=True)


def log_run(job_name, success, detail="", **extra):
    """Appends one JSON line to J:\\Jarvis2\\logs\\<job_name>.jsonl."""
    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "job": job_name,
        "success": success,
        "detail": detail,
        **extra,
    }
    log_file = LOG_DIR / f"{job_name}.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def read_runs(job_name, last_n=None):
    """Reads back all (or the last N) logged runs for a job, newest last."""
    log_file = LOG_DIR / f"{job_name}.jsonl"
    if not log_file.exists():
        return []
    with open(log_file, encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]
    return lines[-last_n:] if last_n else lines


def summarize(job_name, last_n=30):
    """Quick pass/fail count over the last N runs - useful for a status check."""
    runs = read_runs(job_name, last_n=last_n)
    if not runs:
        return f"No runs logged for {job_name!r} yet."
    passed = sum(1 for r in runs if r["success"])
    return f"{job_name}: {passed}/{len(runs)} succeeded (last {len(runs)} runs)."


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) > 1 and sys.argv[1] == "log":
        # log <job_name> <true|false> - detail text read from stdin, avoids all
        # shell-escaping fragility (PowerShell interpolating arbitrary captured
        # output into a command-line string is exactly the kind of thing that broke
        # earlier with em-dashes in .ps1 files - stdin sidesteps that entirely).
        job_name = sys.argv[2]
        success = sys.argv[3].lower() == "true"
        detail = sys.stdin.read()
        log_run(job_name, success, detail)
    elif len(sys.argv) > 1:
        print(summarize(sys.argv[1]))
        for r in read_runs(sys.argv[1], last_n=10):
            print(f"  {r['timestamp']}  {'OK' if r['success'] else 'FAIL'}  {r['detail'][:100]}")
