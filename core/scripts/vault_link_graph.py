# -*- coding: utf-8 -*-
"""
Item 4 of the 2026-08-09 self-improvement brainstorm: a real, if simple,
link-graph over the vault's [[wikilinks]] - surfaces genuinely isolated
notes (nothing links to them, they link to nothing) as candidates worth a
second look, either for cross-linking or for pruning if they turned out to
be dead weight.

Usage:
    python vault_link_graph.py            # prints orphaned + most-linked notes
    python vault_link_graph.py --note "Some Note"   # what links to/from one note
"""
import re
import sys
from collections import defaultdict
from pathlib import Path

VAULT_DIR = Path(r"J:\Jarvis-Brain")
SKIP_DIRS = {".obsidian", ".git"}
LINK_RE = re.compile(r"\[\[([^\]|#]+)")


def build_graph():
    all_notes = {}
    outlinks = defaultdict(set)
    for path in VAULT_DIR.rglob("*.md"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        name = path.stem
        all_notes[name] = path
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for target in LINK_RE.findall(text):
            outlinks[name].add(target.strip())

    inlinks = defaultdict(set)
    for src, targets in outlinks.items():
        for t in targets:
            inlinks[t].add(src)

    return all_notes, outlinks, inlinks


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    all_notes, outlinks, inlinks = build_graph()

    if "--note" in sys.argv:
        target = sys.argv[sys.argv.index("--note") + 1]
        print(f"Links FROM '{target}': {sorted(outlinks.get(target, []))}")
        print(f"Links TO '{target}': {sorted(inlinks.get(target, []))}")
        sys.exit(0)

    orphans = [n for n in all_notes if not outlinks.get(n) and not inlinks.get(n)]
    most_linked = sorted(inlinks.items(), key=lambda kv: -len(kv[1]))[:10]

    print(f"=== {len(all_notes)} notes, {len(orphans)} genuinely isolated (no links in or out) ===\n")
    for n in sorted(orphans):
        print(f"  {n}")
    print(f"\n=== Most-referenced notes ===")
    for name, refs in most_linked:
        if refs:
            print(f"  {name}: linked from {len(refs)} note(s)")
