# -*- coding: utf-8 -*-
"""File the full OPERATION REPORT on an operation's FILE, stamp it DONE / BLOCKED / FAILED and seal it with my JARVIS seal.

    python op_report.py "<card title or action id>" --outcome DONE --summary "what was done, in a few sentences" \\
        --verification LIVE|STRUCTURAL|NONE --verification-note "what was actually checked" \\
        --finding "something learned" --finding "another" --open "what is still open" [--objective "..."] [--subject "..."]
    python op_report.py a_1cdd7eff --stdin < report.json      # same fields as JSON: outcome, summary, verification,
                                                              # verificationNote, findings [..], open [..], objective, subject

The report has the standard sections (reference/serial, subject, objective, summary, procedure, findings, outcome,
verification, open items, prepared by / authorised by). The PROCEDURE is the numbered steps logged with
`op_note.py <ref> "<action>" --kind step --target ... --result ...`, so log steps while working. Verification must say honestly what
was live-verified (watched working), structurally verified (checks pass, not watched) or not verified.
Run it BEFORE action_update.py <id> done/blocked/failed (that keeps this report and only refreshes the stamp); if no report was filed,
action_update builds a minimal one from the logged steps. The text goes to the operation file and the vault report, never the chat log.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from op_note import load_op_files  # noqa: E402

_VALUE_FLAGS = {"--outcome": "outcome", "--summary": "summary", "--verification": "verification", "--verification-note": "verificationNote",
                "--objective": "objective", "--subject": "subject"}
_LIST_FLAGS = {"--finding": "findings", "--open": "open"}


def parse(args):
    """(ref, fields, use_stdin, error)"""
    fields, rest, use_stdin, i = {}, [], False, 0
    while i < len(args):
        a = args[i]
        name, eq, inline = a.partition("=")
        if a == "--stdin":
            use_stdin = True
        elif name in _VALUE_FLAGS or name in _LIST_FLAGS:
            if eq:
                value = inline
            elif i + 1 < len(args):
                i += 1
                value = args[i]
            else:
                return None, fields, use_stdin, f"{name} needs a value"
            if name in _LIST_FLAGS:
                fields.setdefault(_LIST_FLAGS[name], []).append(value)
            else:
                fields[_VALUE_FLAGS[name]] = value
        else:
            rest.append(a)
        i += 1
    return (rest[0] if rest else None), fields, use_stdin, None


def main(argv):
    thin = "--thin" in argv[1:]
    ref, fields, use_stdin, error = parse([a for a in argv[1:] if a != "--thin"])
    if error:
        print(error)
        return 1
    if not ref:
        print(__doc__)
        return 1
    if use_stdin:
        try:
            data = json.loads(sys.stdin.read())
        except ValueError:
            print("--stdin needs a JSON object")
            return 1
        if not isinstance(data, dict):
            print("--stdin needs a JSON object")
            return 1
        data.update(fields)
        fields = data
    mod = load_op_files()
    if mod is None:
        print("op_files.py not found (set JARVIS_UI_DIR to the jarvis-ui checkout)")
        return 1
    ok, message = mod.file_report(ref, fields, thin=thin, enforce=True)
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stdin):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
