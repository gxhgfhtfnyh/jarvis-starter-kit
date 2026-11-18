# -*- coding: utf-8 -*-
"""
Auto-restores critical Jarvis files (CLAUDE.md, settings.json, every hook
script) if any goes missing -- built 2026-08-14 at the owner's request to
harden against something (malware, a targeted attack, an accident)
deleting the security tooling itself, not just tampering with its content.
integrity_check.py already detects a missing file; this is the response
half -- restore it automatically rather than just alert and wait for a
human to notice and fix it by hand.

Restoration source: the scripts folder's own git history (already
auto-committed every turn via scripts_snapshot.ps1) for anything inside
J:\\Jarvis2\\scripts, and the git-tracked `_claude_md_backup.md` copy
(same repo, added this same session to close the gap where CLAUDE.md
itself -- one directory above the repo root -- had zero git protection)
for CLAUDE.md specifically.

Honest limit: this restores from the LATEST known-good git commit, which
is only as current as the last snapshot (every real turn, in practice --
but not literally instantaneous). It does not, and cannot, protect
against the git history itself being wiped by a sufficiently privileged
attacker -- that's what the ACL hardening on `.git` (harden_security_scripts.py)
is for, a second, different layer, not a substitute for this one.

Usage:
    python self_heal_check.py
"""
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from win_toast import show_toast

CLAUDE_MD = Path(__file__).resolve().parent.parent / "CLAUDE.md"
CLAUDE_MD_BACKUP = Path(__file__).resolve().parent / "_claude_md_backup.md"
SETTINGS_JSON = Path.home() / ".claude" / "settings.json"      # derived from the running user's home directory, not a specific machine
SCRIPTS_DIR = Path(__file__).resolve().parent
LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "self_heal.jsonl"

PATH_RE = re.compile(r'([A-Za-z]:[\\/][^\s"]+?\.(?:py|ps1|bat|vbs))')      # backslash or forward slash separators
QUOTED_PATH_RE = re.compile(r'"([A-Za-z]:[\\/][^"]+?\.(?:py|ps1|bat|vbs))"')      # a quoted path may contain spaces


def _hook_paths_from_settings():
    if not SETTINGS_JSON.exists():
        return []
    try:
        data = json.loads(SETTINGS_JSON.read_text(encoding="utf-8-sig"))      # utf-8-sig: PowerShell tools write a BOM that plain utf-8 rejects
    except Exception:
        return []  # can't safely parse a possibly-tampered settings.json -- restoring it comes first, not from itself
    commands = []

    def walk(obj):
        if isinstance(obj, dict):
            if "command" in obj and isinstance(obj["command"], str):
                commands.append(obj["command"])
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)
    paths = set()
    for cmd in commands:
        for found in PATH_RE.findall(cmd) + QUOTED_PATH_RE.findall(cmd):
            paths.add(found.replace("/", chr(92)))      # normalise to backslashes so one file is not listed twice
    return sorted(paths)


def _restore_from_scripts_git(path):
    try:
        rel = path.relative_to(SCRIPTS_DIR)
    except ValueError:
        return False
    result = subprocess.run(
        ["git", "checkout", "HEAD", "--", str(rel)],
        cwd=str(SCRIPTS_DIR), capture_output=True, text=True,
    )
    return result.returncode == 0 and path.exists()


def check():
    restored = []
    still_missing = []

    # CLAUDE.md: restore from its dedicated tracked backup, not scripts git
    # directly (it lives outside that repo's own tree)
    if not CLAUDE_MD.exists():
        if CLAUDE_MD_BACKUP.exists():
            CLAUDE_MD.write_bytes(CLAUDE_MD_BACKUP.read_bytes())
            restored.append(str(CLAUDE_MD))
        else:
            still_missing.append(str(CLAUDE_MD))

    # settings.json has no git backup (lives outside any tracked repo,
    # and is small/rarely-changed enough that this is an honest known gap,
    # not silently pretended-solved) -- flagged, not restored
    if not SETTINGS_JSON.exists():
        still_missing.append(str(SETTINGS_JSON) + " (no backup source available)")

    # every hook script settings.json references, restored from the
    # scripts folder's own git history if it's inside that tree
    for hook_path in _hook_paths_from_settings():
        p = Path(hook_path)
        if p.exists():
            continue
        if SCRIPTS_DIR in p.parents or p.parent == SCRIPTS_DIR:
            if _restore_from_scripts_git(p):
                restored.append(str(p))
            else:
                still_missing.append(str(p) + " (git restore failed or not tracked)")
        else:
            still_missing.append(str(p) + " (outside the scripts git repo, no backup source)")

    result = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "restored": restored,
        "still_missing": still_missing,
    }
    LOG_PATH.parent.mkdir(exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")

    if restored:
        show_toast("Jarvis - files auto-restored", f"{len(restored)} missing file(s) restored: {restored[0]}"[:200])
    if still_missing:
        show_toast("Jarvis - files missing, could not auto-restore", f"{still_missing[0]}"[:200])

    return result


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = check()
    if r["restored"]:
        print(f"[RESTORED] {r['restored']}")
    if r["still_missing"]:
        print(f"[STILL MISSING] {r['still_missing']}")
    if not r["restored"] and not r["still_missing"]:
        print("[OK] all critical files present")
