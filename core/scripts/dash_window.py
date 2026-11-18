# -*- coding: utf-8 -*-
"""The actual pywebview window host for the orchestration dashboard -- a tiny, single-purpose process that
dash_show.py spawns detached (no console, not attached to its caller) rather than ever running inline.

WHY THIS IS A SEPARATE SCRIPT, NOT A FUNCTION INSIDE dash_show.py: pywebview's webview.start() BLOCKS the
calling thread until every window it owns is closed, and on Windows it must be called from the thread that
created the window (the WinForms message loop has to be pumped from there). If dash_show.py called it
inline, `python dash_show.py --monitor 1` would never return -- it would sit there for however many hours
the dashboard stays open. That is exactly the automation-breaking failure mode the owner and Jarvis both
need to avoid: this gets fired from scripts and scheduled tasks that need their shell back immediately.
So dash_show.py launches THIS script as its own detached OS process (the same CREATE_NO_WINDOW |
DETACHED_PROCESS trick it already used to background dashboard_server.py) and returns right away; the
foreground-blocking webview.start() call lives here, in a process whose only job is to be that blocking
loop. Two monitors means two of these processes (one per `dash_show.py --monitor N` invocation) -- pywebview
6.2.1 defaults to private_mode=True with no explicit storage_path, which makes it hand each process its own
fresh, unique temp profile dir (see webview/platforms/winforms.py: init_storage() falls back to
tempfile.TemporaryDirectory() when private_mode is on and storage_path is not set). That is what keeps two
independent dash_window.py processes from fighting over one WebView2 user-data folder; confirmed by actually
running two at once during verification (see dash_show.py's own module docstring / the build notes) rather
than assumed from reading the source alone.

Not meant to be run by hand -- dash_show.py is the entry point that works out the url/title/geometry.
Direct use for debugging only:
    pythonw dash_window.py --url http://127.0.0.1:8787/ --title "Jarvis — Orchestration" --x 0 --y 0 --width 2560 --height 1440
"""
import argparse

import webview

DEFAULT_TITLE = "Jarvis — Orchestration"
# dashboard.css's --rail token (the page's own base background): matching it here means the native window
# shows this colour instead of a flash of white while WebView2 spins up and loads the real page.
BACKGROUND_COLOR = "#1e1f22"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--url", required=True, help="page this window loads, e.g. http://127.0.0.1:8787/")
    ap.add_argument("--title", default=DEFAULT_TITLE, help="Alt-Tab/taskbar title; this is the OS window's "
                     "own Text property (set directly by pywebview), never the page's own <title>, so it "
                     "stays 'Jarvis -- Orchestration' even though the page happens to carry that same title")
    ap.add_argument("--x", type=int, required=True)
    ap.add_argument("--y", type=int, required=True)
    ap.add_argument("--width", type=int, required=True)
    ap.add_argument("--height", type=int, required=True)
    args = ap.parse_args()

    # frameless=True removes the OS titlebar/border -- it is still a real top-level window (pywebview never
    # sets ShowInTaskbar off, so Alt-Tab and the taskbar both show it under --title), just with nothing
    # chrome-like drawn around the page. Two reasons: (1) with a border, x/y/width/height describe the
    # OUTER window including that border, so a bordered window sized to the monitor's own bounds would sit
    # a few px short of the edges on every side -- frameless is what let the old Chrome --app window (which
    # also had no titlebar) actually fill the monitor edge to edge, and this reproduces that. (2) resizable
    # and easy_drag are both off: Jarvis places this window on a specific monitor on purpose (see
    # dash_show.py), not the owner, and with no titlebar to grab, pywebview's default "drag the window by
    # clicking its body" behaviour (easy_drag=True) would otherwise fight with ordinary clicks on the
    # dashboard's own content (channel rows, the flow chart) by moving the window instead of registering them.
    window = webview.create_window(
        title=args.title, url=args.url,
        x=args.x, y=args.y, width=args.width, height=args.height,
        frameless=True, resizable=False, easy_drag=False, confirm_close=False,
        background_color=BACKGROUND_COLOR,
    )
    # One process, one window, one call to start(): the blocking happens HERE, by design -- see the module
    # docstring above for why that is the correct place for it rather than a bug to fix.
    #
    # _fit_to_monitor is passed as webview.start()'s own callback (run on a separate thread while the GUI
    # loop owns the main thread -- see webview/__init__.py: start() does threading.Thread(target=func).start()
    # before it ever blocks) because of a real pixel-accuracy bug found live during this script's own
    # verification: on Windows, pywebview's winforms backend sets the Form's Size from width/height BEFORE it
    # applies frameless=True (self.Size is assigned in BrowserView.__init__ while the form still has its
    # default bordered FormBorderStyle; frameless only strips that border a few lines later) -- so the
    # window actually came up ~16x39px smaller than the monitor (measured via GetWindowRect: 2544x1401
    # instead of the requested 2560x1440), short by roughly a standard caption bar and resize border. A
    # second, explicit move()+resize() after the window is actually shown and already frameless corrects it
    # exactly. Both window.move()/window.resize() are decorated @_shown_call, which BLOCKS until the window's
    # 'shown' event fires -- calling them directly here, before webview.start(), would deadlock (nothing
    # would ever fire 'shown'); routing them through start()'s own callback thread is the documented-safe way.
    def _fit_to_monitor():
        window.move(args.x, args.y)
        window.resize(args.width, args.height)

    webview.start(_fit_to_monitor)


if __name__ == "__main__":
    main()
