# -*- coding: utf-8 -*-
"""Archive the scripts nothing references: cut one of the harness redo (2026-10-30; waits for the owner's yes before --apply).

harness_manifest.py found 135 of 511 files in scripts/ that no hook, scheduled task, document or other script references. Most are old one-offs. This moves them
(with their test files) into J:\\Jarvis2\\archive\\scripts_<date>\\ together with a manifest, so the folder shrinks and nothing is lost: --restore puts every file
back exactly where it was. It never deletes. A file is only moved when ALL of these hold: harness_manifest.py classes it ORPHAN, it was last modified 3 or more
days ago (so tools written tonight are never swept up), and its name appears in no config file under logs/ or in settings.json (a data file may start a script by name).
    python archive_orphans.py                  # show the plan, move nothing
    python archive_orphans.py --apply
    python archive_orphans.py --restore <archive folder>
Pure core (unit tested): select(orphans, ages, protected, min_age_days), with_tests(names, existing)."""
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
LOGS = os.environ.get("HARNESS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
ARCHIVE = os.path.join(os.path.dirname(HERE), "archive")
MANIFEST = "archive_manifest.json"
MIN_AGE_DAYS = 3


def select(orphans, ages, protected, min_age_days=MIN_AGE_DAYS):
    """Pure. -> sorted orphan names that are old enough (age in days from `ages`, unknown age = keep) and not named in `protected`. test_ files are never selected here."""
    out = []
    for n in orphans or []:
        if n.startswith("test_") or n in protected:
            continue
        if ages.get(n) is None or ages[n] < min_age_days:
            continue
        out.append(n)
    return sorted(out)


def with_tests(names, existing):
    """Pure. -> names plus the matching test_<name> file for each that exists, no duplicates, sorted."""
    out = set(names)
    for n in names:
        t = "test_" + n
        if t in existing:
            out.add(t)
    return sorted(out)


def retire_only(names, dispositions):
    """Pure. Keep only the names the file-by-file table marked RETIRE. An orphan is just "not reachable": the table also holds files that are meant to be kept,
    rewired into a sensor, or merged into another file first, and those must not be moved (found 2026-10-30: 56 of the 116 orphans were not RETIRE)."""
    return sorted(n for n in names if str(dispositions.get(n, "")).split(":")[0] == "RETIRE")


SELF_FILES = ("harness_manifest.json", "archive_manifest.json")      # these list every orphan by name, so they must not count as "a config names it"


def _protected(names):
    """Names mentioned in settings.json or any json/txt config under logs/ (a data file can start a script by name)."""
    import glob
    texts = []
    for p in [str(Path.home() / ".claude" / "settings.json")] + glob.glob(os.path.join(LOGS, "*.json")) + glob.glob(os.path.join(LOGS, "*.txt")):
        if os.path.basename(p) in SELF_FILES:
            continue
        try:
            if os.path.getsize(p) < 2_000_000:
                texts.append(open(p, encoding="utf-8", errors="replace").read().lower())
        except OSError:
            continue
    blob = "\n".join(texts)
    return {n for n in names if n.lower() in blob}


def main(argv=None):
    argv = argv or []
    if "--restore" in argv:
        folder = argv[argv.index("--restore") + 1]
        man = json.load(open(os.path.join(folder, MANIFEST), encoding="utf-8"))
        back = 0
        for n in man["files"]:
            src, dst = os.path.join(folder, n), os.path.join(HERE, n)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.move(src, dst)
                back += 1
        print("restored %d of %d file(s) to %s" % (back, len(man["files"]), HERE))
        return 0
    m = json.load(open(os.path.join(LOGS, "harness_manifest.json"), encoding="utf-8"))
    orphans = m["classes"]["orphan"]
    now = time.time()
    ages = {n: (now - os.path.getmtime(os.path.join(HERE, n))) / 86400 for n in orphans if os.path.exists(os.path.join(HERE, n))}
    protected = _protected(orphans)
    chosen = select(orphans, ages, protected)
    if "--retire-only" in argv:
        rows = json.load(open(os.path.join(LOGS, "redo", "file_disposition_final.json"), encoding="utf-8"))
        chosen = retire_only(chosen, {r["file"]: r["disposition"] for r in rows})
    if "--hold" in argv:                           # files a live-reference scan found still used by something that stays (comma separated)
        held = set(argv[argv.index("--hold") + 1].split(","))
        chosen = [n for n in chosen if n not in held]
    files = with_tests(chosen, set(os.listdir(HERE)))
    print("orphans per the manifest: %d | old enough and not named in any config: %d | with their tests: %d file(s)" % (len(orphans), len(chosen), len(files)))
    print("kept back: %d too new, %d named in a config (%s)" % (sum(1 for n in orphans if ages.get(n, 99) < MIN_AGE_DAYS), len(protected), ", ".join(sorted(protected)[:6])))
    if "--apply" not in argv:
        print("Nothing moved. Sample: " + ", ".join(chosen[:12]))
        return 0
    folder = os.path.join(ARCHIVE, "scripts_" + time.strftime("%Y-%m-%d_%H%M"))
    os.makedirs(folder, exist_ok=True)
    moved = []
    for n in files:
        src = os.path.join(HERE, n)
        if os.path.exists(src):
            shutil.move(src, os.path.join(folder, n))
            moved.append(n)
    json.dump({"made": time.strftime("%F %T"), "from": HERE, "files": moved}, open(os.path.join(folder, MANIFEST), "w", encoding="utf-8"), indent=1)
    print("moved %d file(s) to %s\nRestore with: python %s --restore \"%s\"" % (len(moved), folder, os.path.join(HERE, "archive_orphans.py"), folder))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
