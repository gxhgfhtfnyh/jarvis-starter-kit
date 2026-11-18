# -*- coding: utf-8 -*-
"""Phase 1 of the Kyogen pivot (Pi-harness software factory): a deterministic Python
wrapper that owns write enforcement around a Pi coding-agent call. "Agent proposes, code
disposes" -- the agent gets read/edit/write tools only (bash is excluded, see SECURITY
below), this script hashes the watched directory before and after the run and reverts
anything touched outside the declared write-scope, and it runs pytest itself afterward
instead of trusting the agent's own self-report. Approved by the owner 2026-11-16 (Discord,
"i approve all phases"); pattern borrowed from the real disler/super-simple-software-factory
reference repo researched the same night.

SECURITY (two real findings from automated review 2026-11-16, both fixed same night):
1. The first version ran the agent with its default bash tool, a real arbitrary-command/
   network/credential exposure window the after-the-fact hash diff cannot see or undo.
   Fixed: bash excluded from the agent's tools (-xt bash).
2. The first fix then had this script run pytest directly on the agent-authored test file
   -- which EXECUTES agent-authored Python (pytest imports the module before running
   anything in it) with no sandbox, on this real machine, with this real user's full
   filesystem/network/credential access. Excluding the agent's own bash tool means nothing
   if the orchestrating script then runs the agent's code itself. This machine has no
   Docker/container runtime available (checked 2026-11-16), so real sandboxed execution
   isn't available tonight without a bigger infra decision that wasn't asked for. Fixed by
   NOT auto-executing anything: main() only runs the agent (no bash) and enforces the
   write-scope via hash diff, then stops. `run_tests()` still exists but is never called
   automatically -- running it is a separate, explicit, human-reviewed step (see
   run_reviewed_tests.py), the same place the real reference architecture (researched the
   same night) puts its own honest line: "gates check only what a predicate can check...
   the page recommends running the reviewer or reading the output yourself."
Known remaining gap, not fixed tonight: Write/Edit alone can still in principle target an
absolute path outside watch_dir -- the hash-diff only ever sees and can only ever revert
changes inside watch_dir. Full sandboxing (container or VM, no network, read-only mounts)
is real Phase 2 hardening, not pretended to be solved here.

Usage:
    python factory_wrapper.py --task-file <prompt.txt> --write-scope "daily_note_append.py" \
        --write-scope "test_daily_note_append.py" --watch-dir J:\\Jarvis2\\scripts \
        --provider local-qwen --model qwen
    (then read the produced files yourself; only run their tests after you have, since
    nothing here executes agent-authored code automatically)
"""
import argparse
import fnmatch
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

PI_EXE = shutil.which("pi") or "pi"


def _hash_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot(watch_dir):
    """Hash of every file under watch_dir, relative paths as keys. Not recursive into
    .git or __pycache__ -- noise, not signal, for this kind of diff."""
    out = {}
    for root, dirs, files in os.walk(watch_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", "node_modules")]
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, watch_dir).replace("\\", "/")
            try:
                out[rel] = _hash_file(full)
            except OSError:
                pass
    return out


def diff_snapshots(before, after):
    created = [p for p in after if p not in before]
    modified = [p for p in after if p in before and after[p] != before[p]]
    deleted = [p for p in before if p not in after]
    return created, modified, deleted


def in_scope(rel_path, write_scope):
    return any(fnmatch.fnmatch(rel_path, pattern) for pattern in write_scope)


def run_agent(task_text, provider, model, cwd, extra_args=None):
    """No bash: the agent gets read/edit/write only. It cannot run arbitrary commands,
    touch the network, or self-report a test result this script hasn't itself checked.

    The task is written to a temp file and passed as `@<path>` rather than as a raw
    command-line argument -- a real bug found 2026-11-16 during Phase 2 testing: pi's
    Windows .CMD wrapper silently mangles a multi-line string passed inline (the agent
    receives effectively nothing of the real content, with no error), a defect class
    independent of and more severe than any model reliability issue. Reproduced directly
    (a single-line prompt worked; the identical multi-line prompt did not) and confirmed
    the `@file` form reads the full content correctly. This also explains, at least in
    part, the two Phase 1 spec-deviation failures reported as Finding 3 in the research
    paper: the model's own output on both runs is consistent with having received a
    corrupted task, not just with limited capability."""
    fd, task_path = tempfile.mkstemp(suffix=".txt", prefix="factory_task_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(task_text)
        cmd = [PI_EXE, "--provider", provider, "--model", model, "--print", "--no-session",
               "--exclude-tools", "bash"]
        if extra_args:
            cmd.extend(extra_args)
        cmd.append("@" + task_path)
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        try:
            os.remove(task_path)
        except OSError:
            pass


def run_tests(watch_dir, test_pattern):
    """Executes agent-authored code with NO sandbox. Never called from main(). Call this
    directly, yourself, only after you have read the file it will run and decided it is
    safe to execute on this real machine. Returns (passed: bool, output: str)."""
    target = os.path.join(watch_dir, test_pattern)
    if not os.path.exists(target):
        return False, "no test file at " + target
    proc = subprocess.run([sys.executable, "-m", "pytest", test_pattern, "-v"],
                           cwd=watch_dir, capture_output=True, text=True, timeout=120)
    return proc.returncode == 0, proc.stdout + proc.stderr


def enforce(watch_dir, write_scope, before, after):
    """Revert anything outside write_scope. Returns (violations, kept)."""
    created, modified, deleted = diff_snapshots(before, after)
    violations = []
    kept = []
    for rel in created:
        full = os.path.join(watch_dir, rel)
        if in_scope(rel, write_scope):
            kept.append(("created", rel))
        else:
            violations.append(("created", rel))
            try:
                os.remove(full)
            except OSError as e:
                violations.append(("could_not_revert_created", rel + ": " + str(e)))
    for rel in modified:
        if in_scope(rel, write_scope):
            kept.append(("modified", rel))
        else:
            violations.append(("modified_out_of_scope_no_backup_to_restore", rel))
    for rel in deleted:
        violations.append(("deleted", rel))
    return violations, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-file", required=True, help="Path to the task prompt text file")
    ap.add_argument("--write-scope", action="append", required=True,
                     help="Glob pattern (relative to --watch-dir) the agent may create/modify; repeatable")
    ap.add_argument("--watch-dir", required=True)
    ap.add_argument("--provider", default="local-qwen")
    ap.add_argument("--model", default="qwen")
    args = ap.parse_args()

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_text = f.read()

    before = snapshot(args.watch_dir)
    rc, out, err = run_agent(task_text, args.provider, args.model, args.watch_dir)
    after = snapshot(args.watch_dir)
    violations, kept = enforce(args.watch_dir, args.write_scope, before, after)

    print("AGENT_EXIT_CODE:", rc)
    print("AGENT_STDOUT:\n", out)
    if err.strip():
        print("AGENT_STDERR:\n", err)
    print("KEPT (in scope):", kept)
    print("VIOLATIONS (reverted or flagged):", violations)
    print("\nNothing was executed. Read the kept file(s) yourself before running anything "
          "in them -- this machine has no sandbox, so execution is a human decision, not "
          "something this script does for you.")

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
