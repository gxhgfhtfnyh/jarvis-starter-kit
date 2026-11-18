# -*- coding: utf-8 -*-
"""Open an operation of my own from a conversation (Discord, the terminal, Telegram, the UI chat...) so the work is tracked and recorded
on the operations board, with its own file and my stamp, and not only written in the daily note.

    python op_open.py --title "Plan the weekend study" --objective "What this is for, in plain words" --done-when "What done looks like" \\
                      --source discord [--ref <message id or link>] [--priority HIGH] [--type BUILD] [--context "..."] [--due 2026-10-20]
    python op_open.py --title "..." --source terminal --stdin --done-when "..."      # long objective text is read from stdin (handles quotes)
    python op_open.py --list-open                                                    # open operations: id, status, title (add to one instead of a duplicate)
    python op_open.py --title "..." --source telegram --done-when "..." --status done --summary "one plain sentence on what happened"

--source is one of discord, terminal, telegram, ui-chat, other. --status is working (the default: I am on it now), done or blocked
(for something already finished, or waiting on the owner, inside the conversation; needs --summary, and the report is then labelled THIN
unless you add steps afterwards). The card goes on the board marked OPENED BY JARVIS with its source, a tracked job is created (no request
is sent back to me, so nothing loops), and the operation file starts with the opening entry. Then log steps with op_note.py, report progress
with action_update.py, and file the final report with op_report.py, exactly as for a card the owner filed.

Open an operation only for real work or a decision worth tracking (building, fixing, researching, setting something up, a task he asked for).
Do NOT open one for casual chat, jokes or a question answered in one line. If one for the same subject is already open, add to it
(op_note.py) instead. Write in PLAIN ENGLISH: what it is for and why it matters to the owner, not file names or jargon.
"""
import os
import sys
from pathlib import Path

_CANDIDATES = (os.environ.get("JARVIS_UI_DIR"),
               os.path.expanduser(r"~\jarvis-ui\.worktrees\implement-jarvis-archive-terminal"),
               os.path.expanduser(r"~\jarvis-ui"))

FLAGS = {"--title": "title", "--objective": "objective", "--done-when": "outcome", "--context": "context", "--priority": "priority",
         "--type": "type", "--due": "due", "--autonomy": "autonomy", "--notify": "notify", "--constraints": "constraints",
         "--source": "source", "--ref": "ref", "--status": "status", "--summary": "summary"}


def load_op_files():
    """The shared op_files module from the jarvis-ui repo (worktree first, then master). None when it cannot be found."""
    for base in _CANDIDATES:
        if base and (Path(base) / "op_files.py").is_file():
            if base not in sys.path:
                sys.path.insert(0, base)
            import op_files
            return op_files
    return None


def parse(argv):
    opts, flags, i = {}, set(), 0
    args = argv[1:]
    while i < len(args):
        a = args[i]
        if a in ("--stdin", "--list-open"):
            flags.add(a)
        elif a in FLAGS and i + 1 < len(args):
            i += 1
            opts[FLAGS[a]] = args[i]
        elif "=" in a and a.split("=", 1)[0] in FLAGS:
            name, value = a.split("=", 1)
            opts[FLAGS[name]] = value
        else:
            return None, None, a
        i += 1
    return opts, flags, ""


def main(argv):
    opts, flags, bad = parse(argv)
    if opts is None:
        print(f"unknown or incomplete argument: {bad}\n")
        print(__doc__)
        return 2
    mod = load_op_files()
    if mod is None:
        print("op_files.py not found (set JARVIS_UI_DIR to the jarvis-ui checkout)")
        return 1
    if "--list-open" in flags:
        rows = mod.list_open()
        if not rows:
            print("no open operations on the board")
            return 0
        for action_id, status, title in rows:
            print(f"{action_id} | {status} | {title}")
        return 0
    if "--title" not in argv and "title" not in opts:
        print(__doc__)
        return 2
    fields = {k: opts[k] for k in ("title", "objective", "outcome", "context", "priority", "type", "due", "autonomy", "notify", "constraints") if opts.get(k)}
    if "--stdin" in flags:
        text = sys.stdin.read().strip()
        if text:
            fields["objective"] = text
    result = mod.open_operation(fields, opts.get("source", ""), ref=opts.get("ref", ""), status=opts.get("status", "working"),
                                summary=opts.get("summary", ""))
    if not result.get("ok"):
        print("not opened: " + str(result.get("error")))
        return 1
    status = opts.get("status", "working")
    if status == "working":
        # Real gap found 2026-11-06 (the owner: "improve the harness part where you dont stop until
        # work is done instead of waiting for me to txt you"): an operation opened here is tracked
        # on the jarvis-ui board, but keep_going.py (the Stop hook that actually enforces "don't
        # stop") only reads queue.md, goals.json and intent_store.jsonl - it never sees operations
        # at all, so opening one did nothing to keep the turn open. Raising a linked intent here
        # closes that gap for new work going forward. Closing it is still manual (intent_store.py
        # done <id>) when the operation finishes - not yet wired to action_update.py/op_report.py,
        # which live in a separate repo (jarvis-ui) not fully reviewed this session.
        try:
            import subprocess
            outcome = fields.get("outcome", "").strip()
            what = ("Operation %s ('%s') is open: %s" % (result["action"], result["title"], outcome or "finish the work, then close this intent")) [:300]
            subprocess.run(
                [sys.executable, os.path.join(os.path.dirname(__file__), "intent_store.py"), "add",
                 "--kind", "operation_open", "--what", what, "--tier", "1", "--value", "4",
                 "--dedupe", "op-open-" + result["action"]],
                capture_output=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired, KeyError):
            pass
    print(f"opened '{result['title']}' as {result['action']} ({result['status']}). Log steps with: op_note.py {result['action']} "
          f"\"<what you did>\" --kind step --target \"<files>\" --result \"<outcome>\"; report with action_update.py {result['action']} ...")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
