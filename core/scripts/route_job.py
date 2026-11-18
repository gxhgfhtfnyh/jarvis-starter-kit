r"""Route a job: ask the fast free judge (decide.py) whether it is easy_worker, hard_boss or private_boss.
Usage: python route_job.py --name <job> --task "text" [--run]
Privacy first: a quick keyword check marks obvious private material as private_boss BEFORE any model sees the text.
With --run and an easy_worker verdict it hands the job to worker_run.py (backend auto: the local 35B first, OmniRoute otherwise); otherwise it only prints the verdict (the boss does the job).
2026-11-01: a private_boss verdict goes to the LOCAL worker only (worker_run.py --backend local, never OmniRoute) when the local server answers and the job holds no credential-looking text; else the boss keeps it.
Every decision is appended to C:\LocalAI\worker_jobs\route_log.jsonl so hits and misses can be reviewed later."""
import argparse, json, pathlib, re, subprocess, sys, time
from decide import decide

PRIVATE = re.compile(r"password|passwd|api[ _-]?key|token|secret|credential|bank|salary|nric|passport|gmail|inbox|email|telegram|discord dm|private|diary|medical|\bic\b", re.I)
STATE = ("Route this job. easy_worker = small, clear, non-private coding, formatting or data task a weaker free model can do and a boss can check cheaply. "
         "hard_boss = needs deep judgement, big design, tricky debugging or visual/engine work, non-private. private_boss = involves personal or private data.")
LOG = pathlib.Path(r"C:\LocalAI\worker_jobs\route_log.jsonl")


# 2026-10-29: --code-only. The topic-word filter above refuses any job that merely MENTIONS discord, token, email... even when the job is
# "write tests for this script" on fake data (it refused the inbox-hook tests). For code-only jobs the boss asserts the files hold
# only code, and this filter looks for actual private DATA instead: emails, phone numbers, NRIC-style ids, secret-looking strings,
# explicit "password: value" pairs. It scans the task text AND every text file in the job folder.
PRIVATE_DATA = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b[89]\d{7}\b|\b[STFG]\d{7}[A-Z]\b|(?:password|passwd|secret|api[_ -]?key)\s*[:=]\s*\S{4,}|\b(?=[A-Za-z0-9+/=]*\d)(?=[A-Za-z0-9+/=]*[A-Za-z])[A-Za-z0-9+/=]{32,}\b", re.I)  # last part: 32+ char run of letters AND digits (hex/base64 secrets); path-like text with _ or - breaks the run
CODE_FILES = (".py", ".md", ".txt", ".json", ".js", ".ps1", ".csv")
# 2026-11-01 (his word, 04:34 "change"): a private job may go to the LOCAL worker (Qwen 35B-A3B on 127.0.0.1:8090, nothing leaves the PC) but never to OmniRoute.
# Credential-looking text (password=..., long key-like strings) still stays with the boss: a worker has no reason to hold a live credential.
SECRET_DATA = re.compile(r"(?:password|passwd|secret|api[_ -]?key)[\"']?\s*[:=]\s*[\"']?\S{4,}|\b(?=[A-Za-z0-9+/=]*\d)(?=[A-Za-z0-9+/=]*[A-Za-z])[A-Za-z0-9+/=]{32,}\b", re.I)


def has_secret(task, folder=None):
    """True when the task text or a text file in the job folder holds credential-looking data."""
    if SECRET_DATA.search(task or ""):
        return True
    p = pathlib.Path(folder) if folder else None
    if p and p.is_dir():
        for f in p.iterdir():
            if f.is_file() and f.suffix.lower() in CODE_FILES and f.name != "route_out.txt" and SECRET_DATA.search(f.read_text(encoding="utf-8", errors="ignore")):
                return True
    return False


def choose_dispatch(answer, task, folder, local_available):
    """Pure apart from reading the job folder. -> ("worker", [extra worker_run arguments]) or ("boss", why).
    easy_worker goes to the worker (backend auto: local first, OmniRoute otherwise); private_boss goes to the LOCAL worker only, and only without credentials in it."""
    if answer == "easy_worker":
        return "worker", []
    if answer == "private_boss":
        if has_secret(task, folder):
            return "boss", "credential-looking text in the job: the boss keeps it"
        if not local_available:
            return "boss", "private job and the local worker is not running (start C:\\LocalAI\\open_moe35_wt.bat): the boss keeps it"
        return "worker", ["--backend", "local"]
    return "boss", "not a worker job"


def scan_private_data(text):
    """Returns a list of short descriptions of private-looking data found in text (empty when clean)."""
    return sorted({m.group(0)[:3] + "..." for m in PRIVATE_DATA.finditer(text or "")})


def scan_job_folder(folder):
    """Private-data hits in the text files of a job folder: {filename: [hits]}."""
    found = {}
    p = pathlib.Path(folder)
    if p.is_dir():
        for f in p.iterdir():
            if f.is_file() and f.suffix.lower() in CODE_FILES and f.name != "route_out.txt":
                hits = scan_private_data(f.read_text(encoding="utf-8", errors="ignore"))
                if hits:
                    found[f.name] = hits
    return found


JEV_LABELS = {"easy_worker": "a small, clear, non-private coding, test-writing, formatting or data task that a weaker free model can do and a boss can check cheaply",
              "hard_boss": "needs deep judgement, big design, tricky debugging or visual/engine work, but is not private",
              "private_boss": "involves personal or private data, credentials, messages or accounts"}


def jev_route(task):
    """Second opinion from Jev when the free judge gave no answer. Returns a route dict, or None (no key, failure, or confidence under 0.7)."""
    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
        import jev_bench
        a = jev_bench.call_choice(task, "Which kind of job is this?", JEV_LABELS)
    except Exception:
        return None
    if not a or a["choice"] not in JEV_LABELS or a["confidence"] < 0.7:
        return None
    return {"answer": a["choice"], "confidence": a["confidence"], "via": "jev"}


def route(task, code_only=False, folder=None):
    if code_only:
        hits = scan_private_data(task)
        for name, h in (scan_job_folder(folder) if folder else {}).items():
            hits += ["%s: %s" % (name, ", ".join(h))]
        if hits:
            return {"answer": "private_boss", "confidence": 1.0, "via": "data-scan: " + "; ".join(hits)[:200]}
    elif PRIVATE.search(task):
        return {"answer": "private_boss", "confidence": 1.0, "via": "keyword"}
    r = decide(task, STATE, kind="choice", options=["easy_worker", "hard_boss", "private_boss"])
    if r.get("answer") is None and r.get("error") == "empty reply":     # 2026-10-29: the free judge returned nothing for ~700-char test-writing tasks (8 jobs fell to the boss); the first 300 chars carry the whole classification
        r = decide(task[:300], STATE, kind="choice", options=["easy_worker", "hard_boss", "private_boss"])
    r["via"] = "model"
    if r.get("answer") is None:      # 2026-10-29: the free judge keeps returning nothing; Jev (92% vs 68% on intent classification, 0.25 s, about a tenth of a cent per 60 calls) is the second opinion before any fallback
        j = jev_route(task[:600])
        if j:
            return j
    if code_only and r.get("answer") is None and r.get("error") == "empty reply":     # still empty after the shortened retry (3 more jobs on 2026-10-29): the data scan above already cleared privacy, so a test-writing job goes to the free worker instead of silently becoming the boss's work
        return {"answer": "easy_worker", "confidence": 0.7, "via": "code-only default after empty judge reply"}
    if r.get("answer") is None or r.get("confidence", 0) < 0.7:   # unsure -> the boss does it
        r = {"answer": "hard_boss", "confidence": r.get("confidence", 0.0), "via": "fallback:" + str(r.get("error", "low confidence"))}
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True); ap.add_argument("--task", required=True); ap.add_argument("--run", action="store_true")
    ap.add_argument("--code-only", action="store_true", help="boss asserts the job folder holds only code/fake data: scan for private DATA instead of topic words")
    a = ap.parse_args()
    folder = pathlib.Path(r"C:\LocalAI\worker_jobs") / "".join(c if c.isalnum() or c in "-_" else "_" for c in a.name)
    r = route(a.task, code_only=a.code_only, folder=folder)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    task_for_log = "<redacted: private_boss>" if r.get("answer") == "private_boss" else a.task[:300]
    with LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime("%F %T"), "name": a.name, "task": task_for_log, **r}) + "\n")
    print("route:", r["answer"], "confidence", r["confidence"], "via", r["via"])
    local_ok = False
    if r["answer"] == "private_boss":                  # only a private job needs to know whether the local worker is up (an easy job lets worker_run pick the backend)
        try:
            import worker_run
            local_ok = worker_run.pick_backend("local", worker_run.local_up(), worker_run.LOCAL_OFF.exists())[0] == "local"
        except Exception:
            local_ok = False
    kind, extra = choose_dispatch(r["answer"], a.task, folder, local_ok)
    if a.run and kind == "boss" and r["answer"] == "private_boss":
        print("boss keeps it:", extra)
    if a.run and kind == "worker":
        t0 = time.time()
        rc = subprocess.call([sys.executable, str(pathlib.Path(__file__).with_name("worker_run.py")), "--name", a.name, "--task", a.task] + extra)
        with LOG.open("a", encoding="utf-8") as f:  # telemetry (2026-10-29): how long the worker took and how it ended
            f.write(json.dumps({"t": time.strftime("%F %T"), "name": a.name, "event": "worker_done", "secs": round(time.time() - t0), "exit": rc}) + chr(10))
        sys.exit(rc)


if __name__ == "__main__":
    main()
