# -*- coding: utf-8 -*-
"""Meaning-based vault search, the command I actually run (2026-10-29). Thin launcher: memsearch.py needs fastembed, which lives only in the isolated venv
C:\\LocalAI\\embed_trial\\venv, so this runs it there and prints its lines.
    python msearch.py "what port does the omniroute gateway use"
Measured with memory_eval.py: 19 of 20 main questions and 8 of 10 held-out in about one second each (brain.py: 10 of 20 and 5 of 8). First call after a vault edit
re-embeds only the changed notes. Falls back to vsearch.py (keyword search) with a note if the venv is missing.
2026-11-12: tries embed_server.py's warm HTTP server first (real measured subprocess median 1784ms over 50 live calls -- almost entirely interpreter
startup + reloading the model from disk, not the search itself). Falls back to the subprocess path unchanged on any failure.
Pure core (unit tested): build_cmd(args)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = r"C:\LocalAI\embed_trial\venv\Scripts\python.exe"
SERVER_URL = "http://127.0.0.1:8777/search"
SERVER_TIMEOUT = 1.5


def _search_warm(query):
    """-> output text from the warm embed_server.py, or None on any failure (down, timeout, bad response)."""
    import urllib.error
    import urllib.parse
    import urllib.request

    try:
        url = SERVER_URL + "?q=" + urllib.parse.quote(query)
        with urllib.request.urlopen(url, timeout=SERVER_TIMEOUT) as r:
            return r.read().decode("utf-8", errors="replace") if r.status == 200 else None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def build_cmd(args, venv_py=VENV_PY, here=HERE):
    """Command list that runs memsearch.py in the venv, or the keyword search when the venv python is missing."""
    if os.path.exists(venv_py):
        return [venv_py, os.path.join(here, "memsearch.py")] + list(args)
    return [sys.executable, os.path.join(here, "vsearch.py")] + list(args)


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--update" not in argv:
        warm = _search_warm(" ".join(argv))
        if warm is not None:
            print(warm.rstrip())
            return 0
    cmd = build_cmd(argv)
    if "vsearch.py" in cmd[1]:
        print("(embedding venv missing, keyword search used)")
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    print(r.stdout.rstrip())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
