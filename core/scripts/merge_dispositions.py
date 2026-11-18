# -*- coding: utf-8 -*-
"""Merge and check the file-by-file reviewers' output (2026-10-30, harness redo step 1). The ten reviewers each wrote logs/redo/out_NN.jsonl; this checks every
line against the rubric before anything is trusted, then writes one table. A reviewer that skipped a file, invented a field value or covered a file twice is
reported, never silently accepted.
    python merge_dispositions.py            # check + summary
    python merge_dispositions.py --write    # also write logs/redo/file_disposition.json and a markdown table in the vault
Pure core (unit tested): validate_row(row), check_batch(rows, expected), summarize(rows)."""
import glob
import json
import os
import sys
from collections import Counter

REDO = r"J:\Jarvis2\logs\redo"
STAGES = {"PERCEIVE", "DECIDE", "EXECUTE", "LEARN", "MEMORY", "SAFETY", "INTERFACE", "ONEOFF", "DEAD"}
QUALITY = {"good", "ok", "poor", "unknown"}
EFFICIENCY = {"earns its cost", "duplicate", "dead weight", "unknown"}
AUTONOMY = {"acts by itself", "only when asked", "helps the loop", "none"}
FIELDS = ("file", "purpose", "stage", "quality", "efficiency", "autonomy", "disposition", "why", "change")


def disposition_kind(d):
    """Pure. 'MERGE-INTO:x' -> 'MERGE-INTO'; others unchanged; anything else -> None."""
    d = (d or "").strip()
    base = d.split(":", 1)[0]
    return base if base in {"KEEP", "KEEP-REWIRE", "MERGE-INTO", "REWRITE-AS-SENSOR", "RETIRE", "ASK"} else None


def validate_row(row):
    """Pure. -> list of problems for one reviewer row (empty list = fine)."""
    if not isinstance(row, dict):
        return ["not an object"]
    bad = ["missing " + f for f in FIELDS if f not in row]
    if bad:
        return bad
    out = []
    if row["stage"] not in STAGES:
        out.append("stage %r" % row["stage"])
    if row["quality"] not in QUALITY:
        out.append("quality %r" % row["quality"])
    if row["efficiency"] not in EFFICIENCY:
        out.append("efficiency %r" % row["efficiency"])
    if row["autonomy"] not in AUTONOMY:
        out.append("autonomy %r" % row["autonomy"])
    kind = disposition_kind(row["disposition"])
    if kind is None:
        out.append("disposition %r" % row["disposition"])
    elif kind == "MERGE-INTO" and ":" not in row["disposition"]:
        out.append("MERGE-INTO names no target")
    if not str(row["why"]).strip():
        out.append("empty why")
    return out


def check_batch(rows, expected):
    """Pure. -> {'missing': [...], 'extra': [...], 'duplicate': [...], 'bad': [(file, [problems])]} comparing a reviewer's rows with the batch it was given."""
    seen = Counter(r.get("file") for r in rows if isinstance(r, dict))
    exp = set(expected)
    return {"missing": sorted(exp - set(seen)), "extra": sorted(set(seen) - exp), "duplicate": sorted(f for f, n in seen.items() if n > 1),
            "bad": [(r.get("file") if isinstance(r, dict) else "?", validate_row(r)) for r in rows if validate_row(r)]}


def summarize(rows):
    """Pure. -> counts by disposition kind, stage, quality, efficiency and autonomy."""
    return {"disposition": dict(Counter(disposition_kind(r.get("disposition")) or "INVALID" for r in rows)), "stage": dict(Counter(r.get("stage") for r in rows)),
            "quality": dict(Counter(r.get("quality") for r in rows)), "efficiency": dict(Counter(r.get("efficiency") for r in rows)),
            "autonomy": dict(Counter(r.get("autonomy") for r in rows))}


def _read_jsonl(path):
    rows, errs = [], 0
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    errs += 1
    except OSError:
        return None, 0
    return rows, errs


def main(argv=None):
    argv = argv or []
    allrows, problems = [], 0
    for bpath in sorted(glob.glob(os.path.join(REDO, "batch_*.json"))):
        n = os.path.basename(bpath)[6:8]
        expected = [x["file"] for x in json.load(open(bpath, encoding="utf-8"))]
        rows, errs = _read_jsonl(os.path.join(REDO, "out_%s.jsonl" % n))
        if rows is None:
            print("batch %s: no output yet (%d files expected)" % (n, len(expected)))
            problems += 1
            continue
        c = check_batch(rows, expected)
        flag = [k for k in ("missing", "extra", "duplicate", "bad") if c[k]]
        print("batch %s: %d of %d files, unparsable lines %d%s" % (n, len(rows), len(expected), errs, ("  PROBLEMS: " + ", ".join("%s %d" % (k, len(c[k])) for k in flag)) if flag else "  ok"))
        problems += len(flag) + (1 if errs else 0)
        allrows.extend(r for r in rows if isinstance(r, dict))
    s = summarize(allrows)
    print("rows %d | dispositions %s" % (len(allrows), s["disposition"]))
    print("stages %s" % s["stage"])
    if "--write" in argv and not problems:
        json.dump(allrows, open(os.path.join(REDO, "file_disposition.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print("wrote file_disposition.json")
    elif "--write" in argv:
        print("not written: fix the problems above first")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
