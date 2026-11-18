# -*- coding: utf-8 -*-
"""Keep embed_server.py running without anyone having to remember to relaunch it (2026-11-12, his own rule from the
same night: "wtvr u have to remember to do you should force it rather than relying on just remembering to do it").
recall_hook.py and msearch.py already fall back to the slower subprocess path if the warm server is down, so this
is not a correctness gate -- it just keeps the fast path actually fast without anyone noticing it died and manually
relaunching. Tick5 job (every 5 min): a quick /health check, and if it fails, launch embed_server.py detached inside
its venv (same one memsearch.py needs for fastembed).
2026-11-17 real bug found live (whole-day leak, ~18GB across 19 zombie copies before caught and killed by hand):
`is_down()` being true does NOT mean the old process died -- a hung-but-still-alive server also fails /health, and
the old `_launch()` just started a new one on top of it every time, piling up zombies forever since nothing ever
killed the unresponsive one. `_launch()` now kills any existing embed_server.py pythonw.exe process first.
Kill switch: logs/embed_server_watchdog_off.txt.
Pure core (unit tested): is_down(health_result), stale_pids(rows)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
HEALTH_URL = "http://127.0.0.1:8777/health"
HEALTH_TIMEOUT = 1.0
VENV_PYW = r"C:\LocalAI\embed_trial\venv\Scripts\pythonw.exe"
SERVER_SCRIPT = os.path.join(HERE, "embed_server.py")


def is_down(health_result):
    """Pure. health_result is (status_code, body) or None (the check itself failed) -> True when the server needs
    launching."""
    if health_result is None:
        return True
    status, body = health_result
    return status != 200 or body.strip() != "ok"


def _check():
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=HEALTH_TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def stale_pids(rows, script_basename="embed_server.py"):
    """Pure. rows: [(pid, command_line), ...] (as WMI reports them) -> [pid, ...] whose command line runs
    script_basename -- every one of these is a zombie/duplicate server that must die before a new one starts."""
    return [pid for pid, cmd in rows if script_basename in (cmd or "")]


def _list_pythonw_rows():
    """-> [(pid, command_line), ...] for every live pythonw.exe process, or [] on any failure."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | "
             "ForEach-Object { \"$($_.ProcessId)|$($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=10,
        ).stdout
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        pid_s, sep, cmd = line.partition("|")
        if not sep:
            continue
        try:
            rows.append((int(pid_s.strip()), cmd))
        except ValueError:
            pass
    return rows


def _kill_stale_servers():
    """Kill every existing embed_server.py pythonw.exe process. Called right before launching a replacement so a
    hung-but-alive old server can never survive alongside the new one (the real bug, see module docstring)."""
    for pid in stale_pids(_list_pythonw_rows()):
        try:
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
        except Exception:
            pass


def _launch():
    _kill_stale_servers()
    flags = 0x08000000 | 0x00000008  # CREATE_NO_WINDOW | DETACHED_PROCESS
    subprocess.Popen([VENV_PYW, SERVER_SCRIPT], creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None):
    try:
        if os.environ.get("JARVIS_FALLBACK") or os.path.exists(os.path.join(LOGS, "embed_server_watchdog_off.txt")):
            return
        if not os.path.exists(VENV_PYW):
            return  # venv missing entirely: nothing this watchdog can do, callers already fall back to subprocess
        if is_down(_check()):
            _launch()
    except Exception:  # noqa: BLE001 -- never blocks the real turn on a watchdog bug
        return


if __name__ == "__main__":
    main()
