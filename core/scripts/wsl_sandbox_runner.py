# -*- coding: utf-8 -*-
"""Tier 2a, Fable 5.2 review (2026-11-16): real sandboxed execution for Kyogen's factory_wrapper.run_tests(), the one piece Phase 1 explicitly
left as "a separate, explicit, human-reviewed step" because no sandbox was available on this machine. One now is: a dedicated WSL2 distro
("Debian", installed fresh via `wsl --install -d Debian`, never the existing "Ubuntu" distro his coursework tools live in) with automount and
Windows interop disabled in /etc/wsl.conf, running agent-authored code inside bubblewrap (bwrap) as a non-root user with no network, a read-only
root filesystem, and only one bind-mounted directory writable.

Verified live, 2026-11-16 (not just asserted): /mnt/c does not exist inside the sandbox distro at all (ls exits 2, "No such file or directory");
a real socket.connect() to 8.8.8.8:53 raises OSError "Network is unreachable"; writing outside the one bound work directory raises OSError
"Read-only file system"; a script that sleeps past its timeout is killed (exit 124 from the wrapping `timeout`). A legitimate write inside the
bound work directory succeeds normally. These are escape_test_* functions below, re-runnable at any time, not one-off manual checks.

This is a SECOND layer, not a replacement for factory_wrapper.py's own before/after hash check -- run_in_sandbox() copies the declared
write-scope files out after execution, and the caller (run_reviewed_tests.py or similar) is expected to still run enforce() against them exactly
as Phase 1 already does. Sandboxing bounds what the code COULD do while running; the hash check bounds what actually changed on disk afterward.
Both matter; neither replaces the other.

Known limits (same honest line as the rest of this file's history): a bind-mounted docker socket, a SUID binary already present under /usr, or
a container breakout bug in the Linux kernel itself are all out of scope for what bwrap can stop. This closes the specific gap Phase 1 named
("no sandbox available"), not every theoretical sandbox escape.
    python wsl_sandbox_runner.py run --watch-dir <dir> --write-scope "test_x.py" --script test_x.py --timeout 60
    python wsl_sandbox_runner.py escape-tests          # re-run the 4 verification checks and print PASS/FAIL for each
Pure core (unit tested with the real wsl.exe call mocked): build_bwrap_argv(work_dir_wsl, timeout_s, argv)."""
import argparse
import os
import shlex
import subprocess
import sys
import tempfile
import time

DISTRO = "Debian"
SANDBOX_USER = "sandbox"
SANDBOX_HOME_WORK = "/home/sandbox/work"   # the one directory bind-mounted writable inside the sandbox
DEFAULT_TIMEOUT_S = 120
BWRAP_BASE = ["--ro-bind", "/usr", "/usr", "--ro-bind", "/bin", "/bin", "--ro-bind", "/lib", "/lib", "--ro-bind", "/lib64", "/lib64",
              "--ro-bind", "/etc", "/etc", "--tmpfs", "/tmp", "--bind", SANDBOX_HOME_WORK, "/work", "--chdir", "/work",
              "--unshare-net", "--unshare-pid", "--unshare-uts", "--unshare-ipc", "--die-with-parent", "--new-session"]


def build_bwrap_argv(timeout_s, inner_argv):
    """Pure. -> the full wsl.exe argv that runs inner_argv (e.g. ["python3", "test_x.py"]) inside the locked-down sandbox, bounded by `timeout`.
    Separated from the real subprocess call so the exact flags can be asserted on without actually invoking wsl.exe."""
    return (["wsl.exe", "-d", DISTRO, "-u", SANDBOX_USER, "--", "timeout", str(int(timeout_s)), "bwrap"] + BWRAP_BASE + ["--"] + list(inner_argv))


SAFE_REL_PATH = __import__("re").compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-]+)*$")


def _assert_safe_rel_name(rel_name):
    """Real command-injection finding (automated security review, 2026-11-16): building a shell string by interpolating a filename -- even with
    %r -- lets a filename containing shell metacharacters or `..` escape the intended path or inject commands into the root-run staging call.
    Every path is validated against this strict allowlist pattern before it ever reaches a subprocess call, so even a validation bug degrades to
    a rejected path, not code execution. rel_name is currently always produced by os.walk() over a trusted watch_dir, so this is defense in
    depth rather than a currently-exploitable path -- but a general-purpose function must not rely on its one caller being safe.

    2026-11-16, found live: the original fix also passed rel_name as a POSITIONAL shell argument ($1) rather than embedding it, reasoning that
    was strictly safer -- but wsl.exe does not preserve argv as a true array across the Windows/WSL boundary; it rejoins everything into one
    shell string on the Linux side, silently dropping trailing positional arguments entirely (confirmed live: `sh -c 'echo $1' sh file.py`
    printed an empty $1). Positional-arg passing is simply not usable through this boundary. Given that, and that SAFE_REL_PATH already
    excludes every shell metacharacter, embedding the validated name directly (via shlex.quote for a second layer) is both the only path that
    actually works and still safe."""
    if not SAFE_REL_PATH.match(rel_name or ""):
        raise ValueError("unsafe relative path, refusing to stage: %r" % rel_name)


def _wsl_write_file(rel_name, content):
    """Writes one file's content into the sandbox's work dir, staged as the unpriviliged sandbox user (not root, per the same security review).
    rel_name is validated (see _assert_safe_rel_name) and additionally shell-quoted before being embedded in the staging command."""
    _assert_safe_rel_name(rel_name)
    quoted = shlex.quote(rel_name)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content if isinstance(content, bytes) else content.encode("utf-8"))
        with open(tmp_path, "rb") as f:
            data = f.read()
        subprocess.run(["wsl.exe", "-d", DISTRO, "-u", SANDBOX_USER, "--", "sh", "-c",
                        'cd "$HOME/work" && mkdir -p "$(dirname %s)" && cat > %s' % (quoted, quoted)],
                       input=data, check=True)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _wsl_read_file(rel_name):
    """-> bytes, or None if the file does not exist in the sandbox's work dir."""
    _assert_safe_rel_name(rel_name)
    quoted = shlex.quote(rel_name)
    proc = subprocess.run(["wsl.exe", "-d", DISTRO, "-u", SANDBOX_USER, "--", "sh", "-c", 'cd "$HOME/work" && cat %s' % quoted],
                           capture_output=True)
    return proc.stdout if proc.returncode == 0 else None


def _wsl_clear_work_dir():
    subprocess.run(["wsl.exe", "-d", DISTRO, "-u", SANDBOX_USER, "--", "sh", "-c",
                     'cd "$HOME/work" && rm -rf -- * .[!.]* 2>/dev/null; true'])


def run_in_sandbox(watch_dir, write_scope, script_rel, timeout_s=DEFAULT_TIMEOUT_S):
    """Copies watch_dir's files in, runs `python3 <script_rel>` inside the sandbox, copies back only the files matching write_scope (glob
    patterns, same semantics as factory_wrapper.in_scope), and returns (returncode, stdout, stderr, kept_files: {rel: bytes}). Never runs
    anything outside the sandbox; never trusts the sandbox's own exit code as proof the kept files are safe -- the caller still runs
    factory_wrapper.enforce() on what comes back, exactly as Phase 1 already does for the non-sandboxed path."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import factory_wrapper
    _wsl_clear_work_dir()
    for root, _dirs, files in os.walk(watch_dir):
        for name in files:
            full = os.path.join(root, name)
            rel = os.path.relpath(full, watch_dir).replace("\\", "/")
            with open(full, "rb") as f:
                _wsl_write_file(rel, f.read())
    argv = build_bwrap_argv(timeout_s, ["python3", script_rel])
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_s + 15)
    kept = {}
    for root, _dirs, files in os.walk(watch_dir):
        for name in files:
            rel = os.path.relpath(os.path.join(root, name), watch_dir).replace("\\", "/")
            if factory_wrapper.in_scope(rel, write_scope):
                data = _wsl_read_file(rel)
                if data is not None:
                    kept[rel] = data
    return proc.returncode, proc.stdout, proc.stderr, kept


# ---- escape tests: re-runnable live verification, not one-off manual checks ----
# Real bug found running these live, 2026-11-16: wsl.exe does not pass argv as a true execve-style array across the Windows/WSL boundary -- it
# rejoins the argument list into one shell string on the Linux side, so a `python3 -c "..."` argument containing parentheses or quotes gets
# mis-parsed by bash before bwrap or python ever sees it (confirmed: "/bin/bash: -c: line 1: syntax error near unexpected token `('"). The real
# run_in_sandbox() path was never affected (it only ever passes a bare script FILENAME, never inline -c code), but these test helpers originally
# did and needed the same fix already used elsewhere tonight for a different crossing (factory_wrapper.py's pi @file fix): stage the test code
# as a real file inside the sandbox and invoke it by path, never by inline -c text.

def _run_staged_script(code, timeout_s=10):
    """Stages `code` as a python file inside the sandbox's work dir and runs it, avoiding the wsl.exe argv-rejoining bug entirely. -> CompletedProcess."""
    _wsl_write_file("_escape_test.py", code)
    return subprocess.run(build_bwrap_argv(timeout_s, ["python3", "_escape_test.py"]), capture_output=True, text=True)


def escape_test_no_windows_mount():
    r = subprocess.run(build_bwrap_argv(10, ["ls", "/mnt/c"]), capture_output=True, text=True)
    return r.returncode != 0, "ls /mnt/c exit=%d stderr=%s" % (r.returncode, r.stderr.strip())


def escape_test_no_network():
    _wsl_clear_work_dir()
    code = "import socket\ns = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\ns.settimeout(3)\ns.connect(('8.8.8.8', 53))\n"
    r = _run_staged_script(code)
    return r.returncode != 0 and "unreachable" in (r.stderr or "").lower(), r.stderr.strip()[-200:]


def escape_test_write_outside_scope():
    _wsl_clear_work_dir()
    r = _run_staged_script("open('/etc/evil.txt', 'w').write('x')\n")
    return r.returncode != 0 and "read-only" in (r.stderr or "").lower(), r.stderr.strip()[-200:]


def escape_test_timeout_enforced():
    _wsl_clear_work_dir()
    t0 = time.time()
    r = _run_staged_script("import time\ntime.sleep(30)\n", timeout_s=2)
    elapsed = time.time() - t0
    return r.returncode == 124 and elapsed < 10, "exit=%d elapsed=%.1fs" % (r.returncode, elapsed)


def escape_test_legitimate_write_succeeds():
    _wsl_clear_work_dir()
    r = _run_staged_script("open('ok.txt', 'w').write('real work')\n")
    data = _wsl_read_file("ok.txt")
    return r.returncode == 0 and data == b"real work", "exit=%d read_back=%r" % (r.returncode, data)


ESCAPE_TESTS = [
    ("no_windows_mount", escape_test_no_windows_mount),
    ("no_network", escape_test_no_network),
    ("write_outside_scope_blocked", escape_test_write_outside_scope),
    ("timeout_enforced", escape_test_timeout_enforced),
    ("legitimate_write_still_works", escape_test_legitimate_write_succeeds),
]


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("run")
    r.add_argument("--watch-dir", required=True)
    r.add_argument("--write-scope", action="append", required=True)
    r.add_argument("--script", required=True)
    r.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    sub.add_parser("escape-tests")
    args = ap.parse_args()

    if args.cmd == "escape-tests":
        all_ok = True
        for name, fn in ESCAPE_TESTS:
            ok, detail = fn()
            print(("PASS " if ok else "FAIL ") + name + ": " + detail)
            all_ok = all_ok and ok
        return 0 if all_ok else 1

    if args.cmd == "run":
        rc, out, err, kept = run_in_sandbox(args.watch_dir, args.write_scope, args.script, args.timeout)
        print("SANDBOX_EXIT_CODE:", rc)
        print("STDOUT:\n", out)
        if err.strip():
            print("STDERR:\n", err)
        print("KEPT FILES (not yet enforce()-checked):", list(kept))
        return rc

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
