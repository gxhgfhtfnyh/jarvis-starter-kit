#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_op_note.py — file multiple op_note.py steps in one call.

Reads a JSON file (or stdin) of steps, then calls op_note.py once per step
via subprocess. Stops on first failure and reports clearly.

Usage:
    # From a JSON file:
    python batch_op_note.py steps.json

    # From stdin (pipe or heredoc):
    cat steps.json | python batch_op_note.py
    echo '[{"action":"a_123","text":"did stuff"}]' | python batch_op_note.py

    # Dry run (print commands without executing):
    python batch_op_note.py steps.json --dry-run

    # Quiet (suppress op_note.py stdout, only show summary):
    python batch_op_note.py steps.json --quiet

JSON format (one file or piped):
    {
        "op": "a_1cdd7eff",          // action id or card title (optional, can also be per-step)
        "steps": [
            {
                "text": "Patched the lock check in op_files.py",
                "kind": "step",
                "target": "op_files.py",
                "result": "tests pass"
            },
            {
                "text": "Found the key gate was skipped by search",
                "kind": "finding"
            },
            {
                "text": "Refactored the entry parser",
                "kind": "update"
            },
            {
                // step-level op overrides the top-level
                "op": "a_different_action",
                "text": "Another action's note"
            }
        ]
    }

Each step supports: text (required), kind (default: note), target, result, brief.
Per-step "op" overrides the top-level "op".
"""

import json
import subprocess
import sys
import os
from pathlib import Path

# Find python executable (use the same one running this script)
PYTHON = sys.executable

# Find op_note.py — same paths as op_note.py's own search
OP_NOTE_CANDIDATES = [
    r"J:\Jarvis2\scripts\op_note.py",
]


def find_op_note():
    for path in OP_NOTE_CANDIDATES:
        if os.path.isfile(path):
            return path
    print("[ERROR] op_note.py not found at known locations.", file=sys.stderr)
    print("  Edit OP_NOTE_CANDIDATES in this script if it's elsewhere.", file=sys.stderr)
    sys.exit(1)


def parse_steps(data, default_op=None):
    """Parse JSON data (dict or list) into a list of (step_dict, op_string) tuples."""
    steps = []
    if isinstance(data, list):
        # Flat list of step objects
        for item in data:
            op = item.get("op", default_op)
            if op is None:
                print("[WARN] Step has no 'op' field and no top-level 'op' defined:", file=sys.stderr)
                print(f"  {json.dumps(item)[:200]}", file=sys.stderr)
                continue
            steps.append((item, op))
    elif isinstance(data, dict):
        # Structured: { "op": "...", "steps": [...] }
        default_op = data.get("op", default_op)
        for item in data.get("steps", []):
            op = item.get("op", default_op)
            if op is None:
                print("[WARN] Step has no 'op' field:", file=sys.stderr)
                print(f"  {json.dumps(item)[:200]}", file=sys.stderr)
                continue
            steps.append((item, op))
    else:
        print("[ERROR] Expected JSON object or array, got:", type(data).__name__, file=sys.stderr)
        sys.exit(1)
    return steps


def build_argv(op_note_path, op, step, dry_run=False):
    """Build the CLI argv for op_note.py from a step dict."""
    argv = [PYTHON, op_note_path]

    argv.append(str(op))  # action id / card title

    kind = step.get("kind", "note")
    argv.append("--kind")
    argv.append(kind)

    if step.get("target"):
        argv.append("--target")
        argv.append(str(step["target"]))

    if step.get("result"):
        argv.append("--result")
        argv.append(str(step["result"]))

    if step.get("brief"):
        argv.append("--brief")

    # Text is the last positional arg (or --stdin)
    text = step.get("text", "")
    if not text:
        print("[WARN] Step missing 'text' field, skipping", file=sys.stderr)
        return None

    if step.get("stdin"):
        # Text comes from stdin — write to temp file and pipe
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as tmp:
            tmp.write(text)
            tmp_path = tmp.name
        argv.append("--stdin")
        argv.append(f"@{tmp_path}")  # marker to handle in run_step
    else:
        argv.append(text)

    return argv


def run_step(argv, quiet=False, dry_run=False):
    """Execute a single op_note.py call. Returns (ok: bool, output: str, returncode: int)."""
    if dry_run:
        # Just print what would be run
        print(f"  [DRY-RUN] {' '.join(argv)}")
        return (True, "[DRY-RUN]", 0)

    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=120,  # safety valve
        )
        output = result.stdout
        if result.stderr:
            output += "\n[STDERR] " + result.stderr.strip()

        ok = result.returncode == 0
        return (ok, output, result.returncode)
    except subprocess.TimeoutExpired:
        return (False, "[TIMEOUT after 120s]", -1)
    except FileNotFoundError:
        return (False, f"[NOT FOUND] {argv[0]}", -2)
    except Exception as e:
        return (False, f"[ERROR] {e}", -3)


def main():
    # Parse args
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    quiet = "--quiet" in args
    args = [a for a in args if a not in ("--dry-run", "--quiet")]

    if not args:
        print(__doc__)
        sys.exit(1)

    filepath = args[0]

    # Read input
    if filepath == "-":
        # Read from stdin
        raw = sys.stdin.read()
    else:
        filepath = Path(filepath)
        if not filepath.exists():
            print(f"[ERROR] File not found: {filepath}", file=sys.stderr)
            sys.exit(1)
        raw = filepath.read_text(encoding="utf-8")

    # Parse JSON
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"[ERROR] Invalid JSON: {e}", file=sys.stderr)
        sys.exit(1)

    # Extract steps
    steps = parse_steps(data)
    if not steps:
        print("[ERROR] No steps found in input.", file=sys.stderr)
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  batch_op_note — {len(steps)} step(s)")
    print(f"  Dry run: {'YES' if dry_run else 'NO'}")
    print(f"{'='*60}\n")

    # Find op_note.py
    op_note_path = find_op_note()

    # Run each step
    results = []
    for i, (step, op) in enumerate(steps, 1):
        text = step.get("text", "?")
        kind = step.get("kind", "note")
        brief_label = " (BRIEF)" if step.get("brief") else ""
        target_label = f" (target: {step['target']})" if step.get("target") else ""
        result_label = f" (result: {step['result']})" if step.get("result") else ""

        if not quiet:
            print(f"[{i}/{len(steps)}] {op} | {kind}{target_label}{result_label}{brief_label}")
            print(f"  Text: {text[:80]}{'...' if len(text) > 80 else ''}")

        argv = build_argv(op_note_path, op, step, dry_run=dry_run)
        if argv is None:
            results.append((False, "[SKIPPED]", -1))
            continue

        ok, output, rc = run_step(argv, quiet=quiet, dry_run=dry_run)
        results.append((ok, output, rc))

        if not quiet:
            if ok:
                print(f"  [OK] (rc={rc})")
            else:
                print(f"  [FAIL] (rc={rc})")
                if output.strip():
                    for line in output.strip().split("\n")[:5]:
                        print(f"    {line}")

        # Stop on first failure (unless dry-run)
        if not ok and not dry_run:
            print(f"\n{'='*60}")
            print(f"  STOPPED at step {i}/{len(steps)}")
            print(f"{'='*60}")
            break

    # Summary
    print(f"\n{'='*60}")
    print(f"  SUMMARY — {len(results)} step(s)")
    print(f"{'='*60}")
    for i, (ok, output, rc) in enumerate(results, 1):
        status = "OK" if ok else "FAIL"
        print(f"  [{i}] {status} (rc={rc})")

    total = len(results)
    passed = sum(1 for ok, _, _ in results if ok)
    print(f"\n  {passed}/{total} passed")

    if passed < total:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    main()
