# -*- coding: utf-8 -*-
"""Reusable repeated-sampling benchmark for comparing two conditions against a local llama-server
(e.g. a flag on/off, two cache sizes, two quants) -- built 2026-11-02 after a single-run A/B test on
the 35B gave +45-65% one way and then -60% the other way for the identical prompts, minutes apart,
with nothing changed but which way the flag happened to be set: this machine's run-to-run variance
under real background load is bigger than most of the effects we are trying to measure, so a single
run per condition is not evidence. This script runs N repetitions per condition (toggle via an env
var the caller sets between calls, or via two separate server instances on different ports) and
reports mean/median/stdev, not a single number, so a result can actually be trusted or correctly
called inconclusive.

Usage (as a library, from a controlled script you write per comparison -- there is no one-size CLI
because toggling the condition differs per experiment: an env var on an already-running server, two
ports, a restart with a different flag):
    from moe_bench import ask, summarize
    results_off = [ask(port, prompt) for _ in range(N)]
    # ... toggle the condition here (your own code) ...
    results_on = [ask(port, prompt) for _ in range(N)]
    print(summarize("off", results_off))
    print(summarize("on", results_on))
"""
import hashlib
import json
import statistics
import time
import urllib.request


def warmup(port, max_tokens=50):
    """Throw away a first request before measuring -- found live 2026-11-02: the first request after a
    llama-server (re)start measured 11.64 tok/s, the next four measured 63-66 tok/s (stdev 1.27, vs 23.89
    including the cold one). This, not random system-load noise, was the real reason two single-run A/B
    tests on the same flag gave opposite answers minutes apart. Always call this once per server
    (re)start before any comparison; never count its result."""
    ask(port, "Say ready.", max_tokens=max_tokens)


def ask(port, prompt, max_tokens=200, temperature=0, seed=1, timeout=90):
    """One real request to a local OpenAI-compatible /v1/chat/completions endpoint. Returns real
    measured fields only -- never invents a number. wall_s/tok_s are measured here, not read from
    the server's own (sometimes absent) timing fields, so every build of llama-server is comparable."""
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature, "seed": seed, "max_tokens": max_tokens, "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:%d/v1/chat/completions" % port, data=body,
        headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    wall = time.time() - t0
    msg = d["choices"][0]["message"]
    text = msg.get("content") or msg.get("reasoning_content") or ""
    ctoks = d.get("usage", {}).get("completion_tokens", 0)
    return {
        "wall_s": round(wall, 3),
        "completion_tokens": ctoks,
        "tok_s": round(ctoks / wall, 2) if wall > 0 else 0.0,
        "sha1": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
    }


def summarize(label, runs):
    """-> {label, n, mean_tok_s, median_tok_s, stdev_tok_s, all_same_output, sha1s}. stdev is None
    for n<2 (statistics.stdev requires it) rather than raising, since a single-run call is itself
    useful to allow (e.g. for a quick smoke test), just never mistaken for a trustworthy comparison."""
    tok_s = [r["tok_s"] for r in runs]
    sha1s = [r["sha1"] for r in runs]
    return {
        "label": label,
        "n": len(runs),
        "mean_tok_s": round(statistics.mean(tok_s), 2) if tok_s else None,
        "median_tok_s": round(statistics.median(tok_s), 2) if tok_s else None,
        "stdev_tok_s": round(statistics.stdev(tok_s), 2) if len(tok_s) >= 2 else None,
        "all_same_output": len(set(sha1s)) == 1,
        "sha1s": sha1s,
    }


def compare(label_a, runs_a, label_b, runs_b):
    """Honest verdict helper: is B's mean outside A's own spread (mean +/- stdev)? If stdev is None
    (n<2 either side) this always says INCONCLUSIVE -- a single run can never clear this bar, by
    design, which is the whole point of this script existing."""
    sa, sb = summarize(label_a, runs_a), summarize(label_b, runs_b)
    if sa["stdev_tok_s"] is None or sb["stdev_tok_s"] is None:
        verdict = "INCONCLUSIVE (need n>=2 per condition to say anything)"
    else:
        lo, hi = sa["mean_tok_s"] - sa["stdev_tok_s"], sa["mean_tok_s"] + sa["stdev_tok_s"]
        verdict = "REAL DIFFERENCE" if not (lo <= sb["mean_tok_s"] <= hi) else "INCONCLUSIVE (inside A's own spread)"
    return {"a": sa, "b": sb, "verdict": verdict}
