# -*- coding: utf-8 -*-
"""Runs ONE candidate solution in its own fresh, throwaway Python process -- the actual isolation
boundary for hard_test_set.py's grading, after a security review correctly flagged that an
in-process exec() with a trimmed __builtins__ dict is not a real sandbox (routinely escapable via
introspection, e.g. reaching os through object.__subclasses__() without ever touching the blocked
names directly). Not a full container/seccomp sandbox -- not available without extra tooling on
this Windows PC -- but a separate process means an escape lands in a disposable child, not this
session's own live interpreter, and the restricted builtins stay on as a second, cheaper layer.

Invoked by hard_test_set.grade_one() via subprocess, never run by hand:
    python -I hard_test_runner.py <task_id>
reads {"code": "..."} as JSON from stdin, prints {"ok": bool, "error": str|null} as JSON to stdout.
"""
import json
import sys
from pathlib import Path

# -I (isolated mode) deliberately drops the script's own directory from sys.path -- that's part of
# what isolation means, but it also blocks finding this file's sibling module. Re-adding exactly
# this known, trusted script directory (never the cwd) restores that import without touching what
# -I is actually meant to isolate: the candidate code executed inside _exec_and_check.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from hard_test_set import TASKS


def main():
    if len(sys.argv) != 2:
        print(json.dumps({"ok": False, "error": "usage: hard_test_runner.py <task_id>"}))
        return
    task_id = sys.argv[1]
    task = next((t for t in TASKS if t["id"] == task_id), None)
    if task is None:
        print(json.dumps({"ok": False, "error": "unknown task_id %r" % task_id}))
        return
    try:
        payload = json.loads(sys.stdin.read())
        code = payload["code"]
    except Exception as e:
        print(json.dumps({"ok": False, "error": "bad stdin payload: %s" % e}))
        return

    from hard_test_set import _exec_and_check  # local import: only needed in the child process
    ok, err = _exec_and_check(task, code)
    print(json.dumps({"ok": ok, "error": err}))


if __name__ == "__main__":
    main()
