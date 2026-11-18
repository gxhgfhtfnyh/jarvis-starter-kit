# -*- coding: utf-8 -*-
"""Append an index bullet and a body block to an Obsidian daily note in one call, so a new
entry never needs two separate manual edits. Real backlog item (Active Priorities.md, added
2026-10-19); built directly after the Kyogen Phase 1 factory failed this exact spec twice
the same night (see Experiment Log 2026-11-16) -- same spec, built the normal way instead.

Usage:
    python daily_note_append.py --note-path "J:\\Jarvis-Brain\\01 - Daily Notes\\2026-11-16.md" \
        --topic "Session name" --outcome "one-sentence outcome" --body-file body.md
"""
import argparse


def append_entry(note_path, topic, outcome, body_markdown):
    with open(note_path, "r", encoding="utf-8") as f:
        text = f.read()

    lines = text.splitlines(keepends=True)
    index_line = None
    for i, line in enumerate(lines):
        if line.startswith("## Index"):
            index_line = i
            break
    if index_line is None:
        raise ValueError("no ## Index section found")

    # Insert after the LAST existing bullet, not right before the next heading --
    # found live 2026-11-16: inserting right before the heading put the new bullet after a
    # template HTML comment that precedes "## Session 1" in the real daily note shape,
    # jamming it against the heading with no blank line. Appending after the last bullet
    # keeps new entries grouped with the existing ones regardless of what comes after them.
    next_heading = len(lines)
    last_bullet = None
    for i in range(index_line + 1, len(lines)):
        if lines[i].startswith("## "):
            next_heading = i
            break
        if lines[i].startswith("- "):
            last_bullet = i
    insert_at = last_bullet + 1 if last_bullet is not None else next_heading

    bullet = f"- **{topic}** — {outcome}\n"
    lines.insert(insert_at, bullet)

    new_text = "".join(lines)
    if not new_text.endswith("\n"):
        new_text += "\n"
    new_text += "\n" + body_markdown

    with open(note_path, "w", encoding="utf-8") as f:
        f.write(new_text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--note-path", required=True)
    ap.add_argument("--topic", required=True)
    ap.add_argument("--outcome", required=True)
    ap.add_argument("--body-file", required=True)
    args = ap.parse_args()

    with open(args.body_file, "r", encoding="utf-8") as f:
        body_markdown = f.read()

    append_entry(args.note_path, args.topic, args.outcome, body_markdown)
    print(f"Appended to {args.note_path}")


if __name__ == "__main__":
    main()
