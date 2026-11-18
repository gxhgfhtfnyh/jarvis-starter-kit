# -*- coding: utf-8 -*-
"""Stop the port-8090 llama-server (by exact PID, never by name) and relaunch it with a given
argv list, waiting until /health answers. Built 2026-11-03 for the "100 levers" t/s sweep so each
test script doesn't hand-roll its own relauncher -- Test 10b found two real relauncher bugs this
session (Start-Process -RedirectStandardOutput hanging, cmd.exe quoting), so this one uses the
already-proven-working pattern: subprocess.Popen directly (no intermediate cmd.exe/Start-Process
layer), argv as a real list (no shell quoting to get wrong), stdout/stderr to a real file opened
in this process (not -RedirectStandardOutput's own hung pipe).

Usage: python moe_relaunch.py <args...>   e.g. python moe_relaunch.py -fa off
       Always appends the fixed baseline flags; pass ONLY the flags that differ from baseline,
       or pass --full to supply a complete argv yourself (no baseline flags appended).
"""
import subprocess
import sys
import time
import urllib.request

PORT = 8090
EXE = r"C:\LocalAI\llama\llama-server.exe"
MODEL = r"..\models\Qwen3.6-35B-A3B-UD-Q4_K_M.gguf"
LOG = r"C:\LocalAI\server_moe35_test.log"
CWD = r"C:\LocalAI\llama"

# The exact adopted baseline (start_server_moe35.bat), as a flag dict so a sweep can override
# just the one flag under test and keep everything else identical.
BASELINE = {
    "--jinja": None, "-ngl": "99", "-ncmoe": "99", "--moe-cache-mib": "6000",
    "-c": "131072", "-fa": "on", "-ctk": "q8_0", "-ctv": "q8_0",
    "--reasoning-budget": "8000", "-b": "4096", "-ub": "4096", "--cache-reuse": "256",
    "--parallel": "1",
}


def stop():
    ps = (
        "$p=(Get-NetTCPConnection -LocalPort %d -State Listen -ErrorAction SilentlyContinue)."
        "OwningProcess; if($p){Stop-Process -Id $p -Force; 'stopped '+$p}else{'nothing listening'}"
        % PORT
    )
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps],
                          capture_output=True, text=True, timeout=30)
    print("stop:", out.stdout.strip(), out.stderr.strip())
    time.sleep(2)


def health(timeout=1.5):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/health" % PORT, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def start(overrides, full_argv=None):
    if full_argv is not None:
        argv = full_argv
    else:
        flags = dict(BASELINE)
        flags.update(overrides)
        argv = [EXE, "-m", MODEL]
        for k, v in flags.items():
            argv.append(k)
            if v is not None:
                argv.append(str(v))
        argv += ["--host", "127.0.0.1", "--port", str(PORT)]
    print("launch:", " ".join(argv))
    logf = open(LOG, "w")
    subprocess.Popen(argv, cwd=CWD, stdout=logf, stderr=subprocess.STDOUT,
                      creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    for i in range(90):
        if health():
            print("healthy after %ds" % (i * 2))
            return True
        time.sleep(2)
    print("FAILED to come up within 180s, see", LOG)
    return False


if __name__ == "__main__":
    args = sys.argv[1:]
    stop()
    if args and args[0] == "--full":
        ok = start({}, full_argv=[EXE, "-m", MODEL] + args[1:] + ["--host", "127.0.0.1", "--port", str(PORT)])
    else:
        overrides = {}
        i = 0
        while i < len(args):
            k = args[i]
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                overrides[k] = args[i + 1]
                i += 2
            else:
                overrides[k] = None
                i += 1
        ok = start(overrides)
    sys.exit(0 if ok else 1)
