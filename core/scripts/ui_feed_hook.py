# -*- coding: utf-8 -*-
"""
Streams the TERMINAL session's tool activity (the "thinking and coding")
into jarvis-ui's Activities panel. Registered as a PreToolUse hook with
matcher ".*" in ~/.claude/settings.json, so every tool call in the real
session -- reads, greps, edits, commands, web fetches, subagents -- lands
as one short line in J:\\Jarvis2\\logs\\jarvis_ui_feed.jsonl, which
graph_bridge.py tails into the feed.

Silent on any error, exits fast -- never breaks the tool call it rides on.
Each line is appended with a single os.write on an O_APPEND fd (parallel agents cannot
interleave a line); the feed rotates to .1 above 20 MB, the results file above 5 MB.
"""
import difflib
import itertools
import json
import os
import re
import sys
import time
from pathlib import Path

FEED_LOG = Path(os.environ.get("JARVIS_UI_FEED_LOG") or r"J:\Jarvis2\logs\jarvis_ui_feed.jsonl")
RESULT_LOG = Path(os.environ.get("JARVIS_OP_RESULTS_LOG") or r"J:\Jarvis2\logs\jarvis_op_results.jsonl")
MAX_DETAIL = 110
MAX_DIFF_LINES = 60  # real unified diff, capped so one huge Edit can't flood the feed panel
MAX_DIFF_CHARS = 20000  # no diff at all above this many chars (old + new): this hook runs on every tool call and must stay fast
MAX_DIFF_SIDE_LINES = 400
MAX_LINE_BYTES = 48 * 1024  # one feed line never exceeds this
FEED_ROTATE_BYTES = 20 * 1024 * 1024
RESULT_ROTATE_BYTES = 5 * 1024 * 1024


def _edit_diff(ti):
    old = ti.get("old_string") or ""
    new = ti.get("new_string") or ""
    if not old and not new:
        return None
    if len(old) + len(new) > MAX_DIFF_CHARS:
        return None
    a = old.splitlines()
    b = new.splitlines()
    if len(a) > MAX_DIFF_SIDE_LINES or len(b) > MAX_DIFF_SIDE_LINES:
        return None
    lines = list(itertools.islice(difflib.unified_diff(a, b, lineterm="", n=1), MAX_DIFF_LINES + 1))
    if len(lines) > MAX_DIFF_LINES:
        lines = lines[:MAX_DIFF_LINES] + ["… (truncated)"]
    return "\n".join(lines)


_TOKEN = re.compile(r"""(?:[^\s"']|"[^"]*"|'[^']*')+""")
_OP_SPLIT = re.compile(r"(&&|\|\||;|\||&)")
_PY_TESTS = {"pytest", "run_all_tests", "headless_shots", "run_smoke", "py_compile"}
_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _split_segments(command):
    """Token lists, one per simple command, split on && || ; | & outside quotes."""
    segs, cur = [], []
    for tok in _TOKEN.findall(command):
        parts = [tok] if tok[0] in "\"'" else _OP_SPLIT.split(tok)
        for part in parts:
            if part == "":
                continue
            if _OP_SPLIT.fullmatch(part):
                if cur:
                    segs.append(cur)
                cur = []
            else:
                cur.append(part)
    if cur:
        segs.append(cur)
    return segs


def _base(tok):
    t = tok.strip("\"'").replace("\\", "/").rsplit("/", 1)[-1].lower()
    for ext in (".exe", ".cmd", ".bat"):
        if t.endswith(ext):
            t = t[: -len(ext)]
    return t


def is_test_command(command):
    """True when the first real program of a shell command (after cd X &&, NAME=value prefixes) is a test runner.
    Never because a test word is merely an argument of another program (grep, git, echo...)."""
    try:
        for seg in _split_segments(command or ""):
            i = 0
            while i < len(seg) and _ENV_ASSIGN.match(seg[i]):
                i += 1
            if i >= len(seg):
                continue  # only env assignments
            prog = _base(seg[i])
            if prog in ("cd", "pushd", "popd", "export", "set"):
                continue
            args = seg[i + 1:]
            if prog in ("pytest", "py.test", "py_compile"):
                return True
            if prog in ("node", "nodejs"):
                return "--test" in [a.strip("\"'") for a in args]
            if prog in ("python", "py") or re.fullmatch(r"python\d(\.\d+)?", prog):
                j = 0
                while j < len(args):
                    a = args[j].strip("\"'")
                    if a == "-m":
                        return j + 1 < len(args) and _base(args[j + 1]) in _PY_TESTS
                    if a == "-c":
                        return False
                    if a.startswith("-"):
                        j += 1
                        continue
                    name = _base(a)
                    if name.endswith(".py"):
                        name = name[:-3]
                    return name in _PY_TESTS
                return False
            return False
        return False
    except Exception:
        return False


def _append_line(path, entry, rotate_bytes):
    """Append one JSON line with a single os.write on an O_APPEND fd, so parallel writers cannot interleave.
    Rotates to <name>.1 first when the file is oversized (best effort)."""
    blob = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
    if len(blob) > MAX_LINE_BYTES:
        for k in ("diff", "path"):
            entry.pop(k, None)
            blob = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
            if len(blob) <= MAX_LINE_BYTES:
                break
        if len(blob) > MAX_LINE_BYTES:
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if path.stat().st_size > rotate_bytes:
            os.replace(str(path), str(path) + ".1")
    except Exception:
        pass
    if os.name == "nt" and _win_append(str(path), blob):
        return
    fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, blob)
    finally:
        os.close(fd)


def _win_append(path, blob):
    """Atomic append on Windows: the CRT's O_APPEND only seeks to the end before writing, so two processes can overwrite each other.
    A handle opened with FILE_APPEND_DATA (and not FILE_WRITE_DATA) makes the OS place every write at the current end. True when a handle was opened and the write attempted."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateFileW.restype = wintypes.HANDLE
        k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k32.WriteFile.argtypes = [wintypes.HANDLE, wintypes.LPCVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.CreateFileW(path, 0x0004, 0x1 | 0x2 | 0x4, None, 4, 0x80, None)  # FILE_APPEND_DATA, share all, OPEN_ALWAYS, NORMAL
        if h is None or h == wintypes.HANDLE(-1).value:
            return False
        try:
            written = wintypes.DWORD(0)
            ok = k32.WriteFile(h, blob, len(blob), ctypes.byref(written), None)
            return True  # handle was valid: never retry via the CRT path (a retry after a partial write would tear the line)
        finally:
            k32.CloseHandle(h)
    except Exception:
        return False


def detail_of(tool, ti):
    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        return ti.get("file_path") or ti.get("notebook_path") or ""
    if tool in ("Bash", "PowerShell"):
        return ti.get("command", "")
    if tool == "Grep":
        pat = ti.get("pattern", "")
        return f'"{pat}" in {ti.get("path", ".")}'
    if tool == "Glob":
        return ti.get("pattern", "")
    if tool in ("WebFetch",):
        return ti.get("url", "")
    if tool in ("WebSearch",):
        return ti.get("query", "")
    if tool == "Agent":
        return ti.get("description", "")
    if tool == "TaskUpdate":
        return f'task #{ti.get("taskId", "?")} -> {ti.get("status", "")}'
    for k in ("skill", "query", "prompt", "text"):
        if ti.get(k):
            return str(ti[k])
    return ""


def _line_counts(tool, ti):
    """(plus, minus) line counts for an Edit or a Write; None for other tools. Counts only, never the text."""
    try:
        if tool == "Edit":
            old = (ti.get("old_string") or "").splitlines()
            new = (ti.get("new_string") or "").splitlines()
            return len(new), len(old)
        if tool == "Write":
            return len((ti.get("content") or "").splitlines()), 0
    except Exception:
        pass
    return None


def _norm_path(ti):
    p = ti.get("file_path") or ti.get("notebook_path") or ""
    return str(p).replace("\\", "/") if p else ""


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "pre"
    try:
        # same Windows stdin-codepage gap as ui_chat_hook.py -- without
        # this, non-ASCII bytes in tool details mojibake before json.load
        # ever sees them (2026-08-13).
        sys.stdin.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return
    tool = payload.get("tool_name", "")
    if not tool:
        return
    if mode in ("post", "fail"):  # the outcome of a call that the pre hook logged, joined later by tool_use_id (separate file: the feed stays as it was)
        entry = {"ts": time.time(), "id": payload.get("tool_use_id", ""), "ok": mode == "post", "ms": payload.get("duration_ms")}
        _append_line(RESULT_LOG, entry, RESULT_ROTATE_BYTES)
        return
    tool_input = payload.get("tool_input", {}) or {}
    raw_detail = str(detail_of(tool, tool_input))
    detail = raw_detail.replace("\n", " ")
    if len(detail) > MAX_DETAIL:
        detail = detail[: MAX_DETAIL - 1] + "…"
    entry = {"ts": time.time(), "tool": tool, "detail": detail}
    # who made the call and what it touched (added 2026-10-15 for Operation Watch): the subagent's id when a subagent made it, the tool_use_id to join
    # the outcome, the file path, and how many lines an edit added/removed (counts only)
    entry["id"] = payload.get("tool_use_id", "")
    entry["agent"] = payload.get("agent_id", "") or ""
    entry["agent_type"] = payload.get("agent_type", "") or ""
    entry["sid"] = str(payload.get("session_id", ""))[:8]
    path = _norm_path(tool_input)
    if path:
        entry["path"] = path
    counts = _line_counts(tool, tool_input)
    if counts:
        entry["plus"], entry["minus"] = counts
    if tool == "Edit":
        diff = _edit_diff(tool_input)
        if diff:
            entry["diff"] = diff
    if tool in ("Bash", "PowerShell") and is_test_command(raw_detail):
        entry["is_test"] = True
    _append_line(FEED_LOG, entry, FEED_ROTATE_BYTES)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
