# -*- coding: utf-8 -*-
"""Build a slide deck from Markdown using Marp (local, free, no external account).
Adopted 2026-11-16 per the owner's "do your own thing and adopt things that actually make u
work btr" after the super-deep research sweep flagged it as the top real find for the
confirmed real gap: zero deck-generation capability existed before this. Verified live with
a real 4-slide test deck (not just installed and assumed working).

Marp syntax (write this as the .md source):
    ---
    marp: true
    theme: default      # or 'gaia', 'uncover'
    paginate: true
    ---
    # Title slide

    ---               <- "---" on its own line starts a new slide

    ## Section heading
    - bullet
    - bullet

Usage:
    python build_deck.py deck.md              # -> deck.pdf
    python build_deck.py deck.md --pptx        # -> deck.pptx
    python build_deck.py deck.md --html        # -> deck.html
"""
import argparse
import shutil
import subprocess
import sys

NPX_EXE = shutil.which("npx") or "npx"


def build(md_path, fmt="pdf"):
    flag = {"pdf": "--pdf", "pptx": "--pptx", "html": "--html"}[fmt]
    cmd = [NPX_EXE, "--yes", "@marp-team/marp-cli", md_path, flag, "--allow-local-files"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("md_path")
    ap.add_argument("--pptx", action="store_const", dest="fmt", const="pptx")
    ap.add_argument("--html", action="store_const", dest="fmt", const="html")
    ap.set_defaults(fmt="pdf")
    args = ap.parse_args()

    rc, out, err = build(args.md_path, args.fmt)
    print(out)
    if err.strip():
        print(err, file=sys.stderr)
    return rc


if __name__ == "__main__":
    sys.exit(main())
