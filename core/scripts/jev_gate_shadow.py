# -*- coding: utf-8 -*-
"""Jev stop-gate in SHADOW mode (2026-10-29, research pilot 3): it NEVER blocks and never prints. At every Stop it asks Jev one yes/no question about the last ~600
characters of my final message ("does the assistant still have work it could carry on by itself right now?") and logs the answer next to what keep_going.py decided,
to logs/jev_gate_shadow.jsonl. After about 50 stops the two are compared by hand (disagreements) to decide whether Jev should ever gate a stop.
Only my own message tail is sent (no tool output, no files). Kill switch logs/jev_gate_off.txt. Any failure is swallowed. Must run AFTER keep_going.py in the Stop hooks.
Pure core (unit tested): build_body(tail), would_block(noul, hi), read_noul(resp), last_keep_going(path, now, window)."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = r"J:\Jarvis2\logs"
OFF = LOGS + r"\jev_gate_off.txt"
LOG = LOGS + r"\jev_gate_shadow.jsonl"
KG_LOG = LOGS + r"\keep_going.jsonl"
HI = 0.7
QUESTION = ("Does the assistant still have concrete work it could continue by itself right now, using only its own effort, "
            "rather than being finished or only waiting for the user or for a background job to finish?")


def build_body(tail):
    return {"model": "jev-latest", "state": tail, "questions": {"unfinished": {"type": "noul", "instructions": QUESTION}}}


def read_noul(resp):
    return float(resp["answers"]["unfinished"]["noul"])


def would_block(noul, hi=HI):
    """Jev would have blocked the stop when it is at least `hi` sure there is work left."""
    return noul is not None and noul >= hi


def last_keep_going(path=KG_LOG, now=None, window=20):
    """The newest keep_going decision (block flag and reason) if it was written within `window` seconds, else None."""
    now = time.time() if now is None else now
    try:
        last = None
        for line in open(path, encoding="utf-8", errors="ignore"):
            last = line
        r = json.loads(last)
        ts = time.mktime(time.strptime(r["ts"], "%Y-%m-%d %H:%M:%S"))
        return {"block": r.get("block"), "why": r.get("why")} if now - ts <= window else None
    except Exception:
        return None


def last_assistant_tail(transcript_path, n=600):
    text = ""
    with open(transcript_path, encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()[-300:]
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("type") == "assistant":
            for b in (r.get("message") or {}).get("content") or []:
                if isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").strip():
                    text = b["text"]
    return text[-n:]


def main():
    try:
        if os.path.exists(OFF) or os.environ.get("JARVIS_FALLBACK"):
            return
        data = json.load(sys.stdin)
        if data.get("stop_hook_active"):
            return
        tail = last_assistant_tail(data["transcript_path"])
        if len(tail.strip()) < 10:
            return
        sys.path.insert(0, HERE)
        import urllib.request
        import jev_bench
        req = urllib.request.Request(jev_bench.URL, data=json.dumps(build_body(tail)).encode(),
                                     headers={"Authorization": "Bearer " + jev_bench._key(), "Content-Type": "application/json"}, method="POST")
        t = time.time()
        with urllib.request.urlopen(req, timeout=8) as r:
            noul = read_noul(json.loads(r.read().decode()))
        row = {"ts": time.strftime("%F %T"), "noul": noul, "jev_would_block": would_block(noul), "keep_going": last_keep_going(), "ms": round((time.time() - t) * 1000), "tail": tail[-160:].replace("\n", " ")}
        open(LOG, "a", encoding="utf-8").write(json.dumps(row) + "\n")
    except BaseException:
        return


if __name__ == "__main__":
    main()
