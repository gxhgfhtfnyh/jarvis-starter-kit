# -*- coding: utf-8 -*-
"""
One consolidated vault health report - replaces ad-hoc manual audits (the kind
that already caught a stale course count and 5 empty Key People notes, but
only because someone happened to look) with a real, repeatable check.

Checks: orphaned notes, stale "active" notes, status/checkbox contradictions,
frontmatter validation, broken wikilinks, folder-index drift, vault size
growth, duplicate files, empty notes, course-code terminology consistency.

Usage:
    python vault_health_check.py
"""
import datetime as dt
import hashlib
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

VAULT_DIR = Path(os.environ.get("JARVIS_VAULT") or Path(__file__).resolve().parent.parent / "vault")
SKIP_DIRS = {".obsidian", ".git"}
SIZE_LOG = Path(__file__).resolve().parent.parent / "vault_size_history.jsonl"
# Small machine-readable summary the avatar's environmental-signal poll
# (avatar_context_check.py) reads, so a genuinely bad health check result
# can surface as an ambient cue instead of only living in a log file no one
# opens between monthly runs.
SUMMARY_FILE = Path(__file__).resolve().parent.parent / "logs" / ".vault_health_summary.json"

VALID_STATUS = {"active", "completed", "parked", "idea", "archived"}
VALID_PROJECT = {"school", "personal", "meta"}
VALID_TYPE = {"index", "reference", "guide", "plan", "log"}

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
# Known false positives: generic bracket-syntax examples inside prose
# explaining the wikilink convention itself (VAULT-INDEX.md's own docs),
# not real links to a note. Confirmed by hand 2026-08-08, not a bug to fix.
DOC_EXAMPLE_TARGETS = {"links", "old name", "link"}
FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
COURSE_CODE_RE = re.compile(r"\b([AG]\s?-?\s?\d{3})\b")
KNOWN_COURSE_CODES = {"A244", "A245", "A364", "A241", "A232", "A231", "G133", "G135"}


def _all_notes():
    return [p for p in VAULT_DIR.rglob("*.md") if not any(part in SKIP_DIRS for part in p.parts)]


def _parse_frontmatter(text):
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}
    fields = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            fields[key.strip()] = value.strip()
    return fields


def check_frontmatter_and_status(notes):
    issues = []
    for path in notes:
        text = path.read_text(encoding="utf-8", errors="replace")
        fm = _parse_frontmatter(text)
        rel = str(path.relative_to(VAULT_DIR))
        if not fm:
            issues.append(f"{rel}: no frontmatter block")
            continue
        status = fm.get("status")
        project = fm.get("project")
        note_type = fm.get("type")
        if status and status not in VALID_STATUS:
            issues.append(f"{rel}: invalid status {status!r}")
        if project and project not in VALID_PROJECT:
            issues.append(f"{rel}: invalid project {project!r}")
        if note_type and note_type not in VALID_TYPE:
            issues.append(f"{rel}: invalid type {note_type!r}")
        # Status/checkbox contradiction: claims completed but has open items
        if status == "completed" and re.search(r"^\s*-\s*\[ \]", text, re.MULTILINE):
            issues.append(f"{rel}: status=completed but has unchecked '- [ ]' items")
        # last_verified staleness (only if the optional field is present)
        if "last_verified" in fm:
            try:
                verified_date = dt.date.fromisoformat(fm["last_verified"])
                age_days = (dt.date.today() - verified_date).days
                if age_days > 90:
                    issues.append(f"{rel}: last_verified {age_days} days ago (>90)")
            except ValueError:
                issues.append(f"{rel}: malformed last_verified date {fm['last_verified']!r}")
    return issues


def check_empty_notes(notes):
    empty = []
    for path in notes:
        text = path.read_text(encoding="utf-8", errors="replace")
        body = FRONTMATTER_RE.sub("", text, count=1).strip()
        # Strip the title heading and see if anything real remains
        body_no_title = re.sub(r"^#\s+.*$", "", body, count=1, flags=re.MULTILINE).strip()
        if len(body_no_title) < 20:
            empty.append(str(path.relative_to(VAULT_DIR)))
    return empty


def check_broken_links_and_orphans(notes):
    note_stems = {p.stem for p in notes}
    linked_to = set()
    broken = []
    for path in notes:
        text = path.read_text(encoding="utf-8", errors="replace")
        for target in WIKILINK_RE.findall(text):
            target = target.strip()
            linked_to.add(target)
            rel = path.relative_to(VAULT_DIR)
            # Daily notes are a historical log (never edited after the fact) - a
            # since-fixed/deleted link mentioned in old prose isn't a live bug,
            # same reasoning as excluding them from the orphan check below.
            if "01 - Daily Notes" in rel.parts:
                continue
            if target in DOC_EXAMPLE_TARGETS:
                continue
            if target not in note_stems:
                broken.append(f"{rel} -> [[{target}]] (target not found)")

    # Orphans: notes nothing links to, excluding index/root notes and daily notes
    # (daily notes are a log, not meant to be link targets) and Archive (expected quiet)
    orphans = []
    for path in notes:
        rel = path.relative_to(VAULT_DIR)
        if "01 - Daily Notes" in rel.parts or "04 - Archive" in rel.parts:
            continue
        fm = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fm.get("type") == "index":
            continue
        if path.stem not in linked_to:
            orphans.append(str(rel))
    return broken, orphans


def check_folder_index_drift(notes):
    """For each folder with an index note listing '## Notes in this folder',
    compare against the folder's actual .md files."""
    issues = []
    by_folder = defaultdict(list)
    for path in notes:
        by_folder[path.parent].append(path)

    for folder, files in by_folder.items():
        index_candidates = [f for f in files if _parse_frontmatter(
            f.read_text(encoding="utf-8", errors="replace")).get("type") == "index"]
        if not index_candidates:
            continue
        index_note = index_candidates[0]
        index_text = index_note.read_text(encoding="utf-8", errors="replace")
        listed = set(WIKILINK_RE.findall(index_text))
        actual = {f.stem for f in files if f != index_note}
        missing_from_index = actual - listed
        if missing_from_index:
            issues.append(f"{index_note.relative_to(VAULT_DIR)}: not listed - {sorted(missing_from_index)}")
    return issues


def check_stale_active(notes, days_threshold=120):
    stale = []
    cutoff = dt.datetime.now().timestamp() - days_threshold * 86400
    for path in notes:
        fm = _parse_frontmatter(path.read_text(encoding="utf-8", errors="replace"))
        if fm.get("status") == "active" and path.stat().st_mtime < cutoff:
            days = int((dt.datetime.now().timestamp() - path.stat().st_mtime) / 86400)
            stale.append(f"{path.relative_to(VAULT_DIR)}: status=active, unmodified {days}d")
    return stale


def check_duplicate_files():
    hashes = defaultdict(list)
    for path in VAULT_DIR.rglob("*"):
        if path.is_file() and not any(part in SKIP_DIRS for part in path.parts):
            try:
                h = hashlib.md5(path.read_bytes()).hexdigest()
                hashes[h].append(str(path.relative_to(VAULT_DIR)))
            except OSError:
                continue
    return [group for group in hashes.values() if len(group) > 1]


def check_terminology_consistency(notes):
    issues = []
    for path in notes:
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in COURSE_CODE_RE.finditer(text):
            raw = match.group(1)
            normalized = re.sub(r"[\s-]", "", raw).upper()
            if normalized in KNOWN_COURSE_CODES and raw != normalized:
                issues.append(f"{path.relative_to(VAULT_DIR)}: {raw!r} should be {normalized!r}")
    return issues


def check_vault_size_growth():
    notes = _all_notes()
    total_size = sum(p.stat().st_size for p in VAULT_DIR.rglob("*")
                      if p.is_file() and not any(part in SKIP_DIRS for part in p.parts))
    snapshot = {"date": dt.date.today().isoformat(), "note_count": len(notes), "total_bytes": total_size}

    previous = None
    if SIZE_LOG.exists():
        lines = SIZE_LOG.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            previous = json.loads(lines[-1])

    with open(SIZE_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot) + "\n")

    growth_note = ""
    if previous and previous["date"] != snapshot["date"]:
        note_delta = snapshot["note_count"] - previous["note_count"]
        size_delta_mb = (snapshot["total_bytes"] - previous["total_bytes"]) / (1024 * 1024)
        growth_note = f" (since {previous['date']}: {note_delta:+d} notes, {size_delta_mb:+.1f} MB)"

    return snapshot, growth_note


def run_all():
    notes = _all_notes()
    report = {}
    report["frontmatter_status"] = check_frontmatter_and_status(notes)
    report["empty_notes"] = check_empty_notes(notes)
    broken, orphans = check_broken_links_and_orphans(notes)
    report["broken_links"] = broken
    report["orphaned_notes"] = orphans
    report["folder_index_drift"] = check_folder_index_drift(notes)
    report["stale_active"] = check_stale_active(notes)
    report["duplicate_files"] = check_duplicate_files()
    report["terminology"] = check_terminology_consistency(notes)
    report["size_snapshot"], report["growth_note"] = check_vault_size_growth()
    return report


def print_report(report):
    print("=== Vault Health Check ===")
    snap = report["size_snapshot"]
    print(f"{snap['note_count']} notes, {snap['total_bytes'] / (1024*1024):.1f} MB{report['growth_note']}\n")

    sections = [
        ("Frontmatter / status issues", "frontmatter_status"),
        ("Empty notes", "empty_notes"),
        ("Broken wikilinks", "broken_links"),
        ("Orphaned notes (nothing links to them)", "orphaned_notes"),
        ("Folder-index drift", "folder_index_drift"),
        ("Stale 'active' notes (120+ days unmodified)", "stale_active"),
        ("Terminology inconsistencies", "terminology"),
    ]
    total_issues = 0
    for label, key in sections:
        items = report[key]
        total_issues += len(items)
        print(f"[{len(items):>3}] {label}")
        for item in items[:10]:
            print(f"       {item}")
        if len(items) > 10:
            print(f"       ... and {len(items) - 10} more")

    dupes = report["duplicate_files"]
    total_issues += len(dupes)
    print(f"[{len(dupes):>3}] Duplicate file groups")
    for group in dupes[:5]:
        print(f"       {group}")

    print(f"\n{total_issues} total findings.")

    try:
        SUMMARY_FILE.parent.mkdir(exist_ok=True)
        SUMMARY_FILE.write_text(
            json.dumps({"date": dt.date.today().isoformat(), "total_issues": total_issues}),
            encoding="utf-8",
        )
    except OSError:
        pass


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print_report(run_all())
