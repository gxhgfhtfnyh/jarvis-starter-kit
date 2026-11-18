# -*- coding: utf-8 -*-
"""File a note, step, finding, update or form on an operation's FILE (the paper trail the owner pages through on the operations board).

    python op_note.py "<card title or action id a_xxxxxxxx>" "text"                 # a note (default)
    python op_note.py a_1cdd7eff "what I found" --kind update
    python op_note.py a_1cdd7eff "Patched the lock check" --kind step --target "op_files.py" --result "tests pass"
    python op_note.py a_1cdd7eff "the key gate was skipped by search" --kind finding
    python op_note.py "Some card" --kind form --stdin < form.txt                    # long text / text with quotes: read stdin
    python op_note.py --unread                                                      # notes the owner added that I have not read yet
    python op_note.py a_1cdd7eff "ok" --brief                                       # a deliberately short entry (labelled BRIEF in the file)
    python op_note.py a_1cdd7eff "Screenshot of the finished form showing the new attachments area" --attach C:/shots/form.png
                                                                                    # attach pictures, videos or documents (repeat --attach); needs a caption of 4+ words

A note, update or finding needs at least 12 words and a step needs 12 words across its action, --target and --result, or it is
refused with the exact command to file a proper one. File multi-paragraph progress reports with --stdin --kind update using the
sections CURRENT FOCUS / COMPLETED / IN PROGRESS / NEXT / RISKS (the file renders them as a numbered PROGRESS REPORT memo).
Write in PLAIN ENGLISH: what you did and why it matters to the owner, not file names, jargon or code words (he asked for this directly).

Attachments: pictures (png jpg gif webp), videos (mp4 m4v mov webm) and documents (pdf docx xlsx txt md csv), up to 25 MB each (200 MB for
a video). Each file is checked by its real contents, copied into the operation's attachments folder and shown in the file view; the
original is left where it was.

Kinds: note (a remark), update (progress), form (a structured form or checklist; each line stays on its own line),
step (a numbered PROCEDURE step: the text is the action taken, --target the command or file touched, --result what came of it),
finding (something learned). Log steps as you work: the final OPERATION REPORT's procedure is built from them.
Every call also prints any NEW notes the owner added to that operation (and marks them read) - read them before continuing.
action_update.py already files its --note lines and the final stamp (DONE / BLOCKED / FAILED) on the operation by itself;
file the full report with op_report.py. Entries are stored in ARCHIVE_OPS_FILE
(default J:\\Jarvis2\\logs\\ui_operations.json); the text never goes to the chat log.
"""
import os
import sys
from pathlib import Path

_CANDIDATES = (os.environ.get("JARVIS_UI_DIR"),
               os.path.expanduser(r"~\jarvis-ui\.worktrees\implement-jarvis-archive-terminal"),
               os.path.expanduser(r"~\jarvis-ui"))


def load_op_files():
    """The shared op_files module from the jarvis-ui repo (worktree first, then master). None when it cannot be found."""
    for base in _CANDIDATES:
        if base and (Path(base) / "op_files.py").is_file():
            if base not in sys.path:
                sys.path.insert(0, base)
            import op_files
            return op_files
    return None


def main(argv):
    args = argv[1:]
    kind, use_stdin, unread, target, result, rest, i = "note", False, False, "", "", [], 0
    brief = False
    attach = []
    while i < len(args):
        a = args[i]
        if a == "--attach" and i + 1 < len(args):
            i += 1
            attach.append(args[i])
        elif a.startswith("--attach="):
            attach.append(a.split("=", 1)[1])
        elif a == "--stdin":
            use_stdin = True
        elif a == "--unread":
            unread = True
        elif a == "--brief":
            brief = True
        elif a in ("--kind", "--target", "--result") and i + 1 < len(args):
            i += 1
            if a == "--kind":
                kind = args[i]
            elif a == "--target":
                target = args[i]
            else:
                result = args[i]
        elif a.startswith(("--kind=", "--target=", "--result=")):
            name, value = a.split("=", 1)
            if name == "--kind":
                kind = value
            elif name == "--target":
                target = value
            else:
                result = value
        else:
            rest.append(a)
        i += 1
    mod = load_op_files()
    if mod is None:
        print("op_files.py not found (set JARVIS_UI_DIR to the jarvis-ui checkout)")
        return 1
    if unread:
        rows = mod.list_unread()
        if not rows:
            print("no unread notes from the owner")
            return 0
        print(mod.unread_report(rows))
        return 0
    if not rest or (not use_stdin and len(rest) < 2):
        print(__doc__)
        return 1
    ref = rest[0]
    text = sys.stdin.read() if use_stdin else " ".join(rest[1:])
    if attach:
        ok, message = mod.attach_files(ref, attach, text, kind, enforce=True, brief=brief)
    else:
        ok, message = mod.add_entry(ref, text, kind, target=target, result=result, enforce=True, brief=brief)
    print(message)
    if ok:
        warn = claim_check(text, target, result)
        if warn:
            print(warn)
    return 0 if ok else 1


def claim_check(text, target, result):
    """2026-10-29: if a note claims a change (fixed/added/wrote/applied/...), the named files must really have changed
    recently. Prints a warning (never blocks); it would have caught the CLAUDE.md note that was filed after a failed edit."""
    import os
    import re
    import time
    try:
        if not re.search(r"\b(fixed|added|wrote|written|applied|installed|updated|edited|created|built|corrected|changed)\b",
                         " ".join([text, result]), re.I):
            return ""
        stale = []
        for tok in re.split(r"[\s,;+]+", target):
            tok = tok.strip("`'\"()")
            if not tok or "." not in tok:
                continue
            for root in ("", r"J:\Jarvis2\scripts", r"J:\Jarvis2", r"J:\Jarvis2\logs", r"J:\Jarvis-Brain\05 - Resources"):
                p = os.path.join(root, tok) if root else tok
                if os.path.isfile(p):
                    if time.time() - os.path.getmtime(p) > 45 * 60:
                        stale.append(tok)
                    break
        if stale:
            with open(r"J:\Jarvis2\logs\claim_check.log", "a", encoding="utf-8") as f:
                f.write("%s stale-target %s | %s\n" % (time.strftime("%F %T"), stale, text[:120]))
            return "[CLAIM CHECK] this note says something changed, but %s was not modified in the last 45 min. Verify the change really applied before moving on." % ", ".join(stale)
    except Exception:
        pass
    return ""


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
