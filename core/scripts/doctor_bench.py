# -*- coding: utf-8 -*-
"""Planted-bug benchmark for script_doctor (2026-10-29). Takes healthy scripts that have a sibling test file, plants ONE bug, and asks the doctor to
detect and repair it with the real free worker. Reports detected / repaired / accepted-but-wrong per bug kind. Nothing outside a temp folder is touched.
    python doctor_bench.py [--cases N] [--scripts a.py,b.py]
Bug kinds: syntax (drop a colon), undefined (rename a variable use), mangled (non-raw Windows path with a \\r in it), operator (swap a comparison/arith
operator, only tests can see it), import (drop a needed import line).
Pure helpers (plant) are unit tested; the live run uses the worker."""
import os
import re
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import script_doctor as sd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SCRIPTS = ["keep_going.py", "situation.py", "skill_nudge.py", "autopilot.py", "test_nudge.py", "claim_gate.py"]


def plant(kind, text):
    """Returns the broken text, or None when this script offers no place for that bug kind."""
    if kind == "syntax":
        m = re.search(r"^(def \w+\([^)\n]*\)):\n", text, re.M)
        return text.replace(m.group(0), m.group(1) + "\n", 1) if m else None
    if kind == "undefined":
        m = re.search(r"^(    )(\w+) = (.+)\n((?:    .*\n)*?)    return \2\b", text, re.M)
        if m:
            return text.replace("    return %s" % m.group(2), "    return %s_x" % m.group(2), 1)
        return None
    if kind == "mangled":
        return text.rstrip("\n") + '\n\nBROKEN_PATH = "J:\\Jarvis2\\logs\\return_state.json"\n'
    if kind == "operator":
        for a, b in ((" < ", " > "), (" >= ", " < "), (" == ", " != "), (" and ", " or ")):
            i = text.find(a)
            if i > 0 and "def " in text[:i]:
                return text[:i] + b + text[i + len(a):]
        return None
    if kind == "import":
        for m in re.finditer(r"^import (re|json|os|time|sys)\n", text, re.M):
            if re.search(r"\b%s\." % m.group(1), text[m.end():]):          # only an import that the rest of the file really uses
                return text.replace(m.group(0), "", 1)
        return None
    return None


def run(n_cases=6, scripts=None, rounds=3, log=print):
    scripts = scripts or DEFAULT_SCRIPTS
    kinds = ["syntax", "undefined", "mangled", "operator", "import"]
    results = []
    k = 0
    for s in scripts:
        src = os.path.join(HERE, s)
        test = os.path.join(HERE, "test_" + s)
        if not os.path.exists(src):
            continue
        clean = open(src, encoding="utf-8").read()
        for kind in kinds:
            if len(results) >= n_cases:
                break
            if kind in ("operator", "undefined") and not os.path.exists(test):
                continue
            broken = plant(kind, clean)
            if broken is None or broken == clean:
                continue
            tmp = tempfile.mkdtemp(prefix="bench_")
            try:
                shutil.copy(test, tmp) if os.path.exists(test) else None
                for dep in sd.local_deps(src):
                    shutil.copy(dep, tmp)
                path = os.path.join(tmp, s)
                open(path, "w", encoding="utf-8", newline="").write(broken)
                findings = [f for f in sd.check_source(broken, s) if f[0] != "tooling"]
                p, failing, _ = sd.run_tests(tmp, s)
                detected = bool(findings) or failing > 0
                t0 = time.time()
                res = sd.repair(path, rounds=rounds, log=lambda m: None) if detected else {"improved": False, "after": None}
                fixed_text = res.get("fixed_text") or ""
                fixed_ok = False
                if res.get("improved"):
                    open(path, "w", encoding="utf-8", newline="").write(fixed_text)
                    p2, f2, _ = sd.run_tests(tmp, s)
                    after = [f for f in sd.check_source(fixed_text, s) if f[0] != "tooling"]
                    fixed_ok = (not after) and f2 == 0 and (p2 >= p)
                results.append({"script": s, "kind": kind, "detected": detected, "repaired": fixed_ok, "secs": round(time.time() - t0)})
                log("%-16s %-10s detected=%s repaired=%s (%ds)" % (s, kind, detected, fixed_ok, results[-1]["secs"]))
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
    return results


def summarize(results):
    out = {}
    for r in results:
        d = out.setdefault(r["kind"], {"n": 0, "detected": 0, "repaired": 0})
        d["n"] += 1
        d["detected"] += int(r["detected"])
        d["repaired"] += int(r["repaired"])
    return out


def main(argv):
    n = int(argv[argv.index("--cases") + 1]) if "--cases" in argv else 6
    scripts = argv[argv.index("--scripts") + 1].split(",") if "--scripts" in argv else None
    res = run(n, scripts)
    print("kind        n  detected  repaired")
    for k, d in summarize(res).items():
        print("%-10s %2d  %2d        %2d" % (k, d["n"], d["detected"], d["repaired"]))
    tot = len(res)
    print("TOTAL %d cases: detected %d, repaired %d" % (tot, sum(r["detected"] for r in res), sum(r["repaired"] for r in res)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
