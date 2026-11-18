# -*- coding: utf-8 -*-
"""Uncertainty log (2026-11-17, calibration design conversation, topic 2 of 5): a structured way to flag
"genuinely unsure here" so it can be checked later instead of being buried in chat prose, which only gets
caught if the owner happens to notice it live. Research finding this answers: an LLM's own verbalized
confidence ("I'm 90% sure") runs systematically overconfident and is a weak trigger on its own -- the
practitioner consensus is to gate uncertainty structurally rather than trust self-reported confidence text
(arXiv 2609.07395, arXiv 2604.03904, zylos.ai 2026-04-18). This does not ask the model to self-score a
probability (that's the exact unreliable signal the research warns about) -- it just gives a genuine "I
don't actually know this" moment a durable, queryable trail, the same way `claim_gate.py` gives claimed
actions a trail (a related but distinct problem: that one checks claimed DONE work has tool evidence; this
one checks a stated BELIEF gets revisited, not forgotten).
    python uncertainty_log.py log "<claim>" "<why unsure>" [--context "<extra>"]
    python uncertainty_log.py list [--open-only] [--days N]      # print recent entries
    python uncertainty_log.py resolve <id> "<what turned out true>"
Importable: log_uncertain(claim, why, context=""), list_entries(open_only, days), resolve(entry_id, resolution)."""
import json
import os
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "..", "logs", "uncertain_claims.jsonl")


def _read_all():
    if not os.path.exists(LOG_PATH):
        return []
    out = []
    with open(LOG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return out


def _write_all(entries):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def log_uncertain(claim, why, context=""):
    """Appends one entry, returns its id. claim: the thing stated that isn't actually verified. why: the
    real reason it's uncertain (no probe exists, conflicting evidence, guessed from a pattern, etc)."""
    entry = {
        "id": uuid.uuid4().hex[:12],
        "ts": time.time(),
        "claim": claim,
        "why": why,
        "context": context,
        "resolved": False,
        "resolution": None,
        "resolved_ts": None,
    }
    entries = _read_all()
    entries.append(entry)
    _write_all(entries)
    return entry["id"]


def list_entries(open_only=False, days=None):
    entries = _read_all()
    if open_only:
        entries = [e for e in entries if not e["resolved"]]
    if days is not None:
        cutoff = time.time() - days * 86400
        entries = [e for e in entries if e["ts"] >= cutoff]
    return entries


def resolve(entry_id, resolution):
    """Marks one entry resolved with what turned out to be true. Raises KeyError if the id isn't found,
    since silently no-op'ing a resolve for a typo'd id would just hide the entry forever instead."""
    entries = _read_all()
    for e in entries:
        if e["id"] == entry_id:
            e["resolved"] = True
            e["resolution"] = resolution
            e["resolved_ts"] = time.time()
            _write_all(entries)
            return e
    raise KeyError("no uncertainty entry with id %r" % entry_id)


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    cmd = argv[0]
    if cmd == "log":
        if len(argv) < 3:
            print("usage: uncertainty_log.py log \"<claim>\" \"<why unsure>\" [--context \"<extra>\"]")
            return 1
        context = ""
        if "--context" in argv:
            context = argv[argv.index("--context") + 1]
        entry_id = log_uncertain(argv[1], argv[2], context)
        print("logged:", entry_id)
        return 0
    if cmd == "list":
        open_only = "--open-only" in argv
        days = None
        if "--days" in argv:
            days = int(argv[argv.index("--days") + 1])
        for e in list_entries(open_only, days):
            status = "OPEN" if not e["resolved"] else "resolved: " + str(e["resolution"])
            print("[%s] %s -- %s (%s)" % (e["id"], e["claim"], e["why"], status))
        return 0
    if cmd == "resolve":
        if len(argv) < 3:
            print("usage: uncertainty_log.py resolve <id> \"<what turned out true>\"")
            return 1
        try:
            resolve(argv[1], argv[2])
        except KeyError as e:
            print("error:", e)
            return 1
        print("resolved:", argv[1])
        return 0
    print("unknown command:", cmd)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
