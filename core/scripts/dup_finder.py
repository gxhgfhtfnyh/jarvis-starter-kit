# -*- coding: utf-8 -*-
"""Duplicate-purpose finder (2026-10-29, the owner: bloat worry, "are you writing files that already have the same purpose"; Jev research idea 3).
similar_check.py looks for overlap when a script is CREATED, using word overlap of docstrings. This runs over ALL pairs: it takes the most word-similar pairs of
script docstrings and asks Jev one question per pair ("do these two scripts do the same job?"), then prints the pairs Jev is sure about for a human look.
    python dup_finder.py [--top 60] [--min 0.8]
Only our own script docstrings are sent (first 350 characters of each, scanned for private-looking data first). It FINDS candidates only, it never merges or deletes anything.
Pure core (unit tested): candidate_pairs(docs, top), pair_state(a, b)."""
import ast
import itertools
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = r"J:\Jarvis2\scripts"
LOG = r"J:\Jarvis2\logs\dup_finder.jsonl"
sys.path.insert(0, HERE)
import similar_check as sc  # noqa: E402


def load_docs(scripts_dir=SCRIPTS):
    """{file name: docstring} for non-test scripts that have a docstring of at least 8 distinct words."""
    out = {}
    for f in sorted(os.listdir(scripts_dir)):
        if f.endswith(".py") and not f.startswith("test_"):
            try:
                d = ast.get_docstring(ast.parse(open(os.path.join(scripts_dir, f), encoding="utf-8", errors="replace").read())) or ""
            except Exception:
                d = ""
            if len(sc.words(d)) >= 8:
                out[f] = d
    return out


def candidate_pairs(docs, top=60):
    """The `top` most word-similar pairs [(jaccard, file_a, file_b)], best first."""
    ws = {f: sc.words(d) for f, d in docs.items()}
    pairs = [(sc.jaccard(ws[a], ws[b]), a, b) for a, b in itertools.combinations(sorted(ws), 2)]
    return sorted(pairs, reverse=True)[:top]


def pair_state(a, b, limit=350):
    return "SCRIPT A: " + a[:limit] + "\n\nSCRIPT B: " + b[:limit]


def main(argv):
    top = int(argv[argv.index("--top") + 1]) if "--top" in argv else 60
    floor = float(argv[argv.index("--min") + 1]) if "--min" in argv else 0.8
    import jev_bench
    import route_job
    docs = load_docs()
    key, rows = jev_bench._key(), []
    for j, a, b in candidate_pairs(docs, top):
        state = pair_state(docs[a], docs[b])
        if route_job.scan_private_data(state):
            continue
        body = {"model": "jev-latest", "state": state, "questions": {"same": {"type": "noul", "instructions": "Do these two scripts do essentially the same job, so one of them is redundant?"}}}
        try:
            import urllib.request
            req = urllib.request.Request(jev_bench.URL, data=json.dumps(body).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=20) as r:
                noul = float(json.loads(r.read().decode())["answers"]["same"]["noul"])
        except Exception:
            continue
        rows.append({"jaccard": round(j, 2), "a": a, "b": b, "same": noul})
    rows.sort(key=lambda r: -r["same"])
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%F %T"), "pairs": len(rows), "rows": rows}) + "\n")
    hits = [r for r in rows if r["same"] >= floor]
    print("%d pairs asked, %d at %.1f or higher:" % (len(rows), len(hits), floor))
    for r in hits:
        print("  %.2f  jaccard %.2f  %s <-> %s" % (r["same"], r["jaccard"], r["a"], r["b"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
