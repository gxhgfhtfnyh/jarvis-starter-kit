# -*- coding: utf-8 -*-
"""
Item 92 of the 2026-08-09 tools brainstorm: ~50 new scripts were added
across tonight's batches alone, most with hardcoded base paths
(J:\\Jarvis-Brain, J:\\Jarvis2\\scripts) baked in individually rather than
imported from one shared constant. If the vault or scripts folder ever
moves, every hardcoded copy has to be found and fixed by hand. This finds
every distinct hardcoded value for each known base path across the whole
folder, so a real drift (a typo'd path, an old path from before a move)
shows up as more than one distinct value where there should be one.

Usage:
    from path_consistency_check import check_paths
    python path_consistency_check.py
"""
import re
import sys
from pathlib import Path

SCRIPTS_DIR = Path(r"J:\Jarvis2\scripts")
# Each pattern captures the FULL path prefix up through the keyword folder -
# any drive-letter/slash-direction/casing variant will show up as its own
# distinct captured string, which is the actual real signal (more than 1
# distinct form for the same logical location = drift).
KNOWN_BASE_PATHS = {
    "vault root": re.compile(r"[A-Za-z]:[\\/][\w\- .\\/]*?Jarvis-Brain", re.IGNORECASE),
    "scripts folder": re.compile(r"[A-Za-z]:[\\/][\w\- .\\/]*?Jarvis2[\\/]scripts", re.IGNORECASE),
    "avatar folder": re.compile(r"[A-Za-z]:[\\/][\w\- .\\/]*?jarvis-avatar", re.IGNORECASE),
    "voice folder": re.compile(r"[A-Za-z]:[\\/][\w\- .\\/]*?jarvis-voice", re.IGNORECASE),
}


def check_paths():
    # Excludes this file's own source - its KNOWN_BASE_PATHS regex literals
    # contain doubled backslashes that match themselves as a false "second
    # form" of every path, found live testing this exact script.
    self_path = Path(__file__).resolve()
    findings = {}
    for label, pattern in KNOWN_BASE_PATHS.items():
        variants = set()
        for p in SCRIPTS_DIR.glob("*.py"):
            if p.resolve() == self_path:
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            variants.update(pattern.findall(text))
        findings[label] = {"variants_found": sorted(variants)}
    return findings


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for label, info in check_paths().items():
        count = len(info["variants_found"])
        flag = "ok" if count <= 1 else "DRIFT"
        print(f"[{flag}] {label}: {count} distinct form(s) found")
