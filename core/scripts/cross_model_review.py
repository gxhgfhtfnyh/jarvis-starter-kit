# -*- coding: utf-8 -*-
"""Cross-model review (2026-11-05, from tonight's video-digest pile: "a different model family grades the work,
not the same one self-reviewing" - Sprint Ox uses Grok to review Claude's sprints). Sends a changed file to the
rig's local model (a different model family than me) for a second opinion, READ-ONLY - it never edits anything.
Built the same night a rig worker job proved unreliable at autonomous edits: review is a much lower-stakes use of
that model than letting it write code, and its verdict is always a second opinion for the boss to weigh, never
an auto-pass/fail gate on its own.

Usage: python cross_model_review.py --file scripts/attachment_saver.py [--question "what should I check?"] [--backend rig|local]
Pure core (unit tested): build_prompt(file_text, filename, question).
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent

BRIEF = ("You are an independent code reviewer, a different model from whoever wrote this file. Read it and "
         "report real, concrete problems only - correctness bugs, edge cases that would break, anything that "
         "doesn't do what its own docstring/comments claim. Do not restate what the code does. Do not invent "
         "problems to have something to say - if it looks correct, say so plainly. End with one line: "
         "VERDICT: looks correct | VERDICT: real issues found.")


def build_prompt(file_text, filename, question=None):
    q = ("\n\nSpecific thing to check: %s" % question) if question else ""
    return "%s%s\n\nFile: %s\n```\n%s\n```" % (BRIEF, q, filename, file_text)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--question")
    ap.add_argument("--backend", choices=("rig", "local"), default="rig")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args(argv)

    path = pathlib.Path(a.file)
    if not path.exists():
        sys.exit("no file at %r" % a.file)
    prompt = build_prompt(path.read_text(encoding="utf-8"), path.name, a.question)

    import worker_run as wr
    need_local = a.backend == "local"
    need_rig = a.backend == "rig"
    backend, why = wr.pick_backend(a.backend, need_local and wr.local_up(), need_rig and wr.rig_up(), wr.LOCAL_OFF.exists())
    if backend is None:
        sys.exit(why)
    config_dir = wr.CONFIG_LOCAL if backend == "local" else wr.CONFIG_RIG

    env = {"CLAUDE_CONFIG_DIR": config_dir, "PATH": os.environ.get("PATH", ""), "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
           "USERPROFILE": os.environ.get("USERPROFILE", ""), "TEMP": os.environ.get("TEMP", ""), "TMP": os.environ.get("TMP", "")}
    cmd = ["claude", "-p", prompt, "--allowedTools", "", "--output-format", "json"]
    r = subprocess.run(cmd, cwd=str(HERE), env=env, capture_output=True, text=True, timeout=a.timeout, shell=False)
    try:
        result_text = json.loads(r.stdout).get("result", r.stdout)
    except (json.JSONDecodeError, ValueError):
        result_text = r.stdout
    print("backend:", backend, "(%s)" % why)
    print("--- review ---")
    print(result_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
