# -*- coding: utf-8 -*-
"""Canary tool-call test for each OmniRoute chain (2026-10-29). Free models; a few tokens each.
    python provider_canary.py     # one PASS/FAIL line per chain: does it answer, and does it make a tool call?
Key is read from the local key file and never printed."""
import json
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:20128/v1/chat/completions"
KEY = Path(r"C:\LocalAI\omniroute_trial\claude_key.txt").read_text(encoding="utf-8").strip()
CHAINS = ["claude-free", "free-fast", "free-smart"]
TOOL = {"type": "function", "function": {"name": "get_number", "description": "Returns a number. Call it when asked for the number.",
        "parameters": {"type": "object", "properties": {}, "required": []}}}


def probe(model):
    body = json.dumps({"model": model, "max_tokens": 80, "tools": [TOOL], "tool_choice": "auto",
                       "messages": [{"role": "user", "content": "Call the get_number tool now."}]}).encode()
    req = urllib.request.Request(BASE, body, {"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    t = time.time()
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.load(r)
    msg = d["choices"][0]["message"]
    return bool(msg.get("tool_calls")), time.time() - t, d.get("model", "?")


bad = 0
for m in CHAINS:
    try:
        ok, secs, used = probe(m)
        if not ok:  # free models are flaky: one retry before calling it a miss
            ok, secs, used = probe(m)
        print(("PASS" if ok else "WARN"), m, f"tool call {'made' if ok else 'NOT made'} in {secs:.1f}s via {used}")
        bad += 0 if ok else 1
    except Exception as e:
        print("FAIL", m, type(e).__name__, str(e)[:120])
        bad += 1
sys.exit(1 if bad else 0)
