# -*- coding: utf-8 -*-
"""Adopt a worker-written test file into the repo without breaking the suite (2026-10-29).
    python adopt_worker_tests.py JOBDIR test_file.py DEST_NAME.py [--drop-failing]
Steps: scan for side effects (scan_worker_tests, refuses on any finding); run the file inside JOBDIR; for every FAILED test add
@pytest.mark.skip(reason="worker expectation disagrees with the module: review") above it so the failure stays visible and is not lost
(default), or with --drop-failing remove those tests entirely (for worker tests that patch attributes the module does not have: junk, not a
to-review list); write the result to scripts/DEST_NAME and print the tally.
Pure core (unit tested): failed_ids(pytest_output), add_skips(source, failing), drop_tests(source, failing)."""
import ast
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import scan_worker_tests as sw  # noqa: E402


RISKY_ROOTS = {"subprocess", "os", "requests", "urllib", "shutil", "socket", "webbrowser", "ctypes"}


def import_side_effects(source):
    """Line numbers of module-level statements (not inside def, class or an if block) that call into subprocess, os, requests, urllib, shutil, socket,
    webbrowser or ctypes: importing such a module DOES something. 2026-10-29: a worker test imported exam_pack_reminder.py, which sends a Discord message
    at import, and the owner got four real reminders."""
    hits = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return hits
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.If, ast.Import, ast.ImportFrom)):
            continue
        value = node.value if isinstance(node, (ast.Expr, ast.Assign, ast.AnnAssign)) else None
        for sub in ast.walk(value) if value is not None else ():
            if isinstance(sub, ast.Call):
                chain, f = [], sub.func
                while isinstance(f, ast.Attribute):
                    chain.append(f.attr)
                    f = f.value
                if isinstance(f, ast.Name) and f.id in RISKY_ROOTS and not (f.id == "os" and chain and chain[-1] in ("path", "environ", "getenv", "sep", "name")):
                    hits.append(node.lineno)
                    break
    return hits


GUARD_TOP = '''# --- adoption guard (process level): while a test of this file runs, the interpreter itself refuses to start a process ---
import sys as _guard_sys

_GUARD_ON = {"on": False}      # switched on by the autouse fixture only while one of this file's own tests runs (it used to start True and blocked process starts in every test that ran BEFORE this file, found 2026-10-30)
_GUARD_EVENTS = ("subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn", "os.startfile")


def _guard_audit(event, args):
    if _GUARD_ON["on"] and event in _GUARD_EVENTS:
        raise RuntimeError("blocked by the adoption guard: " + event)


_guard_sys.addaudithook(_guard_audit)
# --- end guard ---
'''

GUARD = '''

@pytest.fixture(autouse=True)
def _no_real_subprocess(monkeypatch):
    """Safety net added at adoption: a test that forgets to fake subprocess.run fails loudly instead of running a real command."""
    import subprocess as _sp

    def _boom(*a, **k):
        raise AssertionError("real subprocess call attempted by a test")
    monkeypatch.setattr(_sp, "run", _boom)
    monkeypatch.setattr(_sp, "Popen", _boom)
    monkeypatch.setattr(_sp, "check_output", _boom)
    _GUARD_ON["on"] = True
    yield
    _GUARD_ON["on"] = False
'''


def only_plain_subprocess_import(test_source, module_source=""):
    """True only when the test imports subprocess in the plain form (no 'from subprocess import', no alias) and the tested module does not bind
    subprocess functions by name either (a name bound before the guard patches the module would escape it). Security review 2026-10-29."""
    for src in (test_source, module_source):
        try:
            tree = ast.parse(src)
        except SyntaxError:
            return False
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "subprocess":
                return False
            if src is test_source and isinstance(node, ast.Import):
                if any(a.name.split(".")[0] == "subprocess" and a.asname for a in node.names):
                    return False
    return True


def add_subprocess_guard(source):
    """Add the process-level audit hook at the very top (before any import runs) and the autouse fixture at the end (idempotent)."""
    if "_no_real_subprocess" in source:
        return source
    if not re.search(r"^import pytest|^from pytest", source, re.M):
        source = "import pytest\n" + source
    return GUARD_TOP + "\n" + source.rstrip("\n") + "\n" + GUARD


FAILURE_LOG = r"J:\Jarvis2\logs\dropped_tests.jsonl"


def failure_messages(output):
    """{test name: short failure text} from the 'FAILED path::test - message' lines of a pytest -q run."""
    out = {}
    for m in re.finditer(r"^FAILED\s+\S+?::(?:\w+::)?(\w+)(?:\s+-\s+(.*))?$", output, re.M):
        out[m.group(1)] = (m.group(2) or "")[:300]
    return out


def log_failures(test_file, output, failing, action, path=None):
    """Append one line per failing worker test (file, name, failure text, what adoption did) so a later pilot has real labelled cases. Never raises."""
    try:
        import json
        import time
        msgs = failure_messages(output)
        with open(path or FAILURE_LOG, "a", encoding="utf-8") as f:
            for _cls, name in failing:
                f.write(json.dumps({"ts": time.strftime("%F %T"), "file": test_file, "test": name, "message": msgs.get(name, ""), "action": action}) + "\n")
    except Exception:
        pass


def failed_ids(output):
    """[(class or '', test name)] from 'FAILED path::Class::test - msg' lines of a pytest -q run."""
    out = []
    for m in re.finditer(r"^FAILED\s+\S+?::(?:(\w+)::)?(\w+)", output, re.M):
        out.append((m.group(1) or "", m.group(2)))
    return out


def add_skips(source, failing):
    """Insert a skip decorator above each failing test function; returns (new_source, number_added). Parametrized or duplicate names are matched by name."""
    names = {n for _, n in failing}
    tree = ast.parse(source)
    lines = source.split("\n")
    inserts = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            inserts.append((first, node.col_offset))
    for first, col in sorted(inserts, reverse=True):
        lines.insert(first - 1, " " * col + '@pytest.mark.skip(reason="worker expectation disagrees with the module: review")')
    new = "\n".join(lines)
    if inserts and not re.search(r"^import pytest|^from pytest", new, re.M):
        new = "import pytest\n" + new
    return new, len(inserts)


def drop_tests(source, failing):
    """Remove the failing test functions entirely. Returns (new_source, number_dropped). A class whose every method was dropped gets a pass body."""
    names = {n for _, n in failing}
    tree = ast.parse(source)
    lines = source.split("\n")
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.append((first, node.end_lineno))
    for a, b in sorted(spans, reverse=True):
        del lines[a - 1:b]
    new = "\n".join(lines)
    try:
        ast.parse(new)
    except SyntaxError:
        out = []
        pending_class = False
        for ln in new.split("\n"):
            if pending_class and ln.strip() and not ln.startswith((" ", "\t")):
                out.append("    pass")            # the class above has no body left
            if pending_class and ln.strip():
                pending_class = False
            out.append(ln)
            if re.match(r"class \w+.*:\s*$", ln):
                pending_class = True
        if pending_class:
            out.append("    pass")
        new = "\n".join(out)
    return new, len(spans)


def main(argv):
    drop = "--drop-failing" in argv
    argv = [a for a in argv if a != "--drop-failing"]
    if len(argv) != 3:
        print(__doc__)
        return 1
    job, test, dest = argv
    # Security review 2026-11-18: every scan below reads the worker's TEST file, but pytest itself auto-executes
    # a conftest.py (or honors a pytest/tox/setup config) sitting anywhere under JOBDIR before any test runs,
    # which would run arbitrary code regardless of what the scans found. Refuse outright rather than scan for it.
    _unsafe_names = ("conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml")
    _found = [n for n in _unsafe_names if os.path.exists(os.path.join(job, n))]
    if _found:
        print("REFUSED: %s contains %s -- pytest would run it before any scan sees it" % (job, ", ".join(_found)))
        return 6
    path = os.path.join(job, test)
    findings = sw.scan_source(open(path, encoding="utf-8", errors="replace").read())
    test_text = open(path, encoding="utf-8", errors="replace").read()
    module_file = os.path.join(job, test[len("test_"):])
    module_text = open(module_file, encoding="utf-8", errors="replace").read() if os.path.exists(module_file) else ""
    if findings and all("imports subprocess" in msg for _ln, msg in findings) and only_plain_subprocess_import(test_text, module_text):      # a test that only IMPORTS subprocess (plain form, to fake it) is allowed once a process-level guard makes any real start fail loudly
        guarded = add_subprocess_guard(test_text)
        open(path, "w", encoding="utf-8", newline="").write(guarded)
        findings = [f for f in sw.scan_source(guarded) if "imports subprocess" not in f[1]]
    if findings:
        for ln, msg in findings:
            print("REFUSED %s:%d %s" % (test, ln, msg))
        return 2
    module_path = os.path.join(job, test[len("test_"):])
    if os.path.exists(module_path):
        effects = import_side_effects(open(module_path, encoding="utf-8", errors="replace").read())
        if effects:
            print("REFUSED %s: importing the module runs code at lines %s (subprocess, os, network...); test it by reading the source, never import it" % (test, effects[:5]))
            return 5
    import script_doctor      # 2026-10-29 (the owner: the checker applies to what free workers write too): a test file with syntax, import or undefined-name problems is refused
    def _real(findings):      # unused imports and unused variables are lint, not breakage: do not refuse a working worker test for them
        return [f for f in findings if "imported but unused" not in f[2] and "never used" not in f[2]]
    bad = _real(script_doctor.check_source(open(path, encoding="utf-8", errors="replace").read(), test))
    if bad:       # try the automatic repair first (on the job-folder copy only); refuse only if it is still broken
        print("DOCTOR found %d problem(s); trying the repair script" % len(bad))
        try:
            script_doctor.repair(path, rounds=2, apply=True, log=lambda *_: None)
        except Exception as e:
            print("DOCTOR repair failed: %s" % e)
        bad = _real(script_doctor.check_source(open(path, encoding="utf-8", errors="replace").read(), test))
    if bad:
        for k, ln, m in bad[:6]:
            print("DOCTOR %s line %s: %s" % (k, ln, m))
        return 4
    r = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", "--noconftest", "-c", os.devnull, test],
        cwd=job, capture_output=True, text=True, timeout=300,
    )
    src = open(path, encoding="utf-8").read()
    failing = failed_ids(r.stdout + r.stderr)
    log_failures(test, r.stdout + r.stderr, failing, "dropped" if drop else "skipped")      # labelled material for judging failing worker tests later (Jev coding-loop idea)
    new, n = drop_tests(src, failing) if drop else add_skips(src, failing)
    dest_path = os.path.join(HERE, dest)
    open(dest_path, "w", encoding="utf-8", newline="").write(new)
    print("wrote %s: %d failing tests %s: %s" % (dest, n, "DROPPED" if drop else "marked skip (to review)", ", ".join(t for _, t in failing)[:300]))
    r2 = subprocess.run([sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider", dest], cwd=HERE, capture_output=True, text=True, timeout=300)
    print("adopted file in the repo:", (r2.stdout.strip().splitlines() or ["?"])[-1])
    return 0 if r2.returncode == 0 else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
