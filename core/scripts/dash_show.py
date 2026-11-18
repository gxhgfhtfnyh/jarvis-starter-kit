# -*- coding: utf-8 -*-
"""Open the orchestration dashboard in its own NATIVE window on a chosen monitor (rewritten 2026-11-02:
the owner rejected the first version outright -- "it should be an app not smt in chrome or html or wtvr
funny format" -- so this no longer shells out to Chrome/Edge at all. The dashboard's own HTML/CSS/JS
(dashboard_static/*) and its backend (dashboard_server.py) are both untouched; the only thing that changed
is what hosts the page: a real pywebview window (a Windows Form around a WebView2 control) instead of a
browser in --app mode. It shows up in Alt-Tab and the taskbar as "Jarvis -- Orchestration", with no address
bar, no tabs, and no Chrome/Edge icon anywhere, because there is no browser process involved any more.
Still starts dashboard_server.py first if it is not already answering, exactly as before.

    python dash_show.py                        # opens on monitor 1 (leftmost, by Windows' own X coordinate)
    python dash_show.py --monitor 2             # opens on monitor 2
    python dash_show.py --monitor 2 --view /x   # opens a specific path on that monitor (default "/")
    python dash_show.py --monitor 2 --close     # closes whatever this script last opened on that monitor

Monitors are read live from Windows (no hardcoded resolution), identified by their left-to-right order
(1 = leftmost) -- unchanged from the Chrome-mode version, same get_monitors(). "Leftmost" is Windows' own
Bounds.X, which is NOT the same thing as "primary": checked live on his actual two-monitor setup
(2026-11-02) and the portrait secondary sits at x=-1440 while the landscape primary sits at x=0, so on this
PC right now --monitor 1 is the PORTRAIT screen and --monitor 2 is the landscape primary -- worth knowing
before assuming "1" always means the big horizontal one. State (which window's PID is on which monitor) is
still kept in logs\\dash_windows.json, same file/shape, so --close works the same way it always did; the PID
tracked is now the detached dash_window.py process's PID (see below), not a browser's.

WHY THIS SCRIPT NEVER BLOCKS: pywebview's webview.start() has to run on the thread that created the window
and sits there pumping Windows messages until the window closes -- on Windows there is no way around that.
If this script called it directly, `python dash_show.py --monitor 1` would hang for however long the
dashboard stays open, which breaks exactly the kind of unattended/scripted launch the owner and Jarvis both
need ("fire this from a script or scheduled task without the whole system hanging"). So the window itself
is created and run by dash_window.py, launched here as its own detached process (no console, not attached
to this one -- the same CREATE_NO_WINDOW | DETACHED_PROCESS + stdio-to-DEVNULL pattern this script already
used to background dashboard_server.py, for the same reason: a browser/GUI process that outlives this
script must never share a pipe/handle this script's own output is on, or whatever is reading that output
waits on the long-lived process instead of just this short-lived one). This script's own job ends the
moment that detached process exists and its PID is recorded -- it prints and returns immediately.

TWO MONITORS, TWO PROCESSES: the owner has two real monitors (2560x1440 landscape primary, 1440x2560 portrait
secondary) and wants different content controllable per screen. pywebview 6.2.1 *can* run several windows
in one process (several webview.create_window() calls before one webview.start()), but that is not the
shape of this CLI: `--monitor 1` and `--monitor 2` are two separate invocations, i.e. two separate OS
processes, each spawning its own dash_window.py child with its own single window. That turns out to be fine
on this version/platform -- verified by actually running both at once, not just assumed -- because pywebview
defaults to private_mode=True with no storage_path, which hands each process a fresh, unique temp profile
directory (see dash_window.py's docstring for the exact source line). Two independent processes with two
independent WebView2 profiles never contend for the same user-data folder."""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

LOGS = Path(r"J:\Jarvis2\logs")
STATE = LOGS / "dash_windows.json"
SCRIPTS = Path(__file__).parent
SERVER_SCRIPT = SCRIPTS / "dashboard_server.py"
WINDOW_SCRIPT = SCRIPTS / "dash_window.py"
PORT = 8787
TITLE = "Jarvis \u2014 Orchestration"

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def get_monitors():
    """[{x,y,w,h}], left-to-right order, via a one-line PowerShell call (no extra Python deps). Unchanged
    from the Chrome-mode launcher: still the simplest reliable way to get LIVE monitor geometry on Windows
    (no hardcoded resolution, so it keeps working if monitors are added, removed or rearranged) without
    pulling in a whole GUI toolkit in this process just to ask that one question."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", _MONITOR_PS], capture_output=True, text=True, timeout=15).stdout
    return _parse_monitor_output(out)


_MONITOR_PS = ("Add-Type -AssemblyName System.Windows.Forms; [System.Windows.Forms.Screen]::AllScreens | "
               "ForEach-Object { \"$($_.Bounds.X),$($_.Bounds.Y),$($_.Bounds.Width),$($_.Bounds.Height)\" }")


def _parse_monitor_output(text):
    """Split out from get_monitors() so the parsing itself -- the part that can actually go wrong (a stray
    blank line, an unexpected sort order) -- is testable without shelling out to PowerShell in a unit test."""
    mons = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        x, y, w, h = (int(v) for v in line.split(","))
        mons.append({"x": x, "y": y, "w": w, "h": h})
    mons.sort(key=lambda m: m["x"])
    return mons


def server_alive(timeout=6.0):
    # timeout defaults to 6s, not a snappier 1-2s, because /api/status is not a cheap endpoint: it fans out
    # to several local health checks (dashboard_server.py's own gather_live_checks() docstring documents a
    # closed localhost port on this PC "not always failing fast", with one real response measured past
    # 4.5 s). Measured live while building this launcher: a plain request to a dashboard_server.py that was
    # unquestionably healthy still regularly took ~1.5 s to answer, which made a 1.5 s client timeout here a
    # coin flip that this launcher kept losing -- server_alive() reported "down" for a server that was up
    # and would have answered fine one beat later, and ensure_server() then "fixed" that by starting a
    # SECOND dashboard_server.py (see its own comment below for how badly that went).
    try:
        urllib.request.urlopen("http://127.0.0.1:%d/api/status" % PORT, timeout=timeout)
        return True
    except Exception:
        return False


def ensure_server():
    # Even with server_alive()'s own timeout fixed (see above), a single failed check still is not trusted
    # before deciding the server is down and spawning a second one -- belt and suspenders, because getting
    # this wrong is expensive: confirmed live during this launcher's 2026-11-02 verification that spawning a
    # redundant dashboard_server.py while a real one is still running makes things WORSE, not better.
    # http.server sets allow_reuse_address, which lets a second process bind the SAME port on Windows with
    # no "address already in use" error, and `Get-NetTCPConnection -LocalPort 8787` showed the OS actually
    # handed the listening socket back and forth between the two PIDs afterward -- two servers silently
    # fighting over one port is a strictly worse failure than taking a couple of extra seconds to be sure.
    for _ in range(3):
        if server_alive():
            return
        time.sleep(1.0)
    subprocess.Popen([sys.executable, str(SERVER_SCRIPT)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      close_fds=True, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)
    for _ in range(20):
        if server_alive():
            return
        time.sleep(0.5)
    raise RuntimeError("dashboard_server.py did not come up in 10 s")


def load_state():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_state(st):
    STATE.write_text(json.dumps(st), encoding="utf-8")


def pythonw_path():
    """The windowless twin of whichever interpreter is running this script, so the detached window-host
    process never flashes a console of its own (CREATE_NO_WINDOW already suppresses one, but pythonw.exe is
    belt-and-suspenders and matches how every other daemon in this codebase is launched -- see jarvis-habits:
    'daemons via a one-shot scheduled task with the full pythonw.exe path'). Falls back to the interpreter
    actually running (python.exe) if no pythonw.exe sits next to it, which still works fine under
    CREATE_NO_WINDOW; it would just be a second-choice fallback rather than the usual path."""
    candidate = Path(sys.executable).with_name("pythonw.exe")
    return str(candidate) if candidate.is_file() else sys.executable


def close_monitor(monitor):
    st = load_state()
    pid = st.pop(str(monitor), None)
    save_state(st)
    if pid:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        print("closed pid %s on monitor %d" % (pid, monitor))
    else:
        print("nothing tracked on monitor %d" % monitor)


def open_monitor(monitor, view):
    mons = get_monitors()
    if monitor < 1 or monitor > len(mons):
        print("monitor %d does not exist (found %d: %s)" % (monitor, len(mons), mons))
        sys.exit(1)
    m = mons[monitor - 1]
    ensure_server()
    url = "http://127.0.0.1:%d%s" % (PORT, view)

    st = load_state()
    old_pid = st.get(str(monitor))
    if old_pid:
        # Replacing whatever was already on this monitor, same as the Chrome version did -- taskkill is a
        # hard kill, not a graceful window.destroy(), but that was already true before this rewrite and
        # --close/re-open were never meant to be gentle, just reliable.
        subprocess.run(["taskkill", "/F", "/PID", str(old_pid)], capture_output=True)

    # Hand off to dash_window.py as a fully detached process -- see this module's own docstring for why
    # (webview.start() blocks; this script must not). close_fds + DEVNULL stdio for the same reason the old
    # Chrome launch needed it: a GUI process that outlives this script must not inherit a pipe/handle this
    # script's own stdout/stderr is on, or whatever is reading THIS script's output ends up waiting on the
    # long-lived window process instead of just this short one.
    args = [pythonw_path(), str(WINDOW_SCRIPT), "--url", url, "--title", TITLE,
            "--x", str(m["x"]), "--y", str(m["y"]), "--width", str(m["w"]), "--height", str(m["h"])]
    p = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          close_fds=True, creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS)
    st[str(monitor)] = p.pid
    save_state(st)
    print("opened %s on monitor %d (%dx%d at %d,%d), pid %d" % (url, monitor, m["w"], m["h"], m["x"], m["y"], p.pid))


def main():
    monitor = 1
    if "--monitor" in sys.argv:
        monitor = int(sys.argv[sys.argv.index("--monitor") + 1])
    view = "/"
    if "--view" in sys.argv:
        view = sys.argv[sys.argv.index("--view") + 1]
    if "--close" in sys.argv:
        close_monitor(monitor)
        return
    open_monitor(monitor, view)


if __name__ == "__main__":
    main()
