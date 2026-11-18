# -*- coding: utf-8 -*-
"""PreToolUse hook: a real gate for destructive filesystem actions, not just prose in CLAUDE.md.

v1, SHADOW MODE ONLY by default -- this has not been watched against real traffic yet, so it never
blocks until the owner says to flip logs/delete_gate_mode.txt to "enforce", the same rollout
send_gate_hook.py went through before it was trusted. Built 2026-11-06 after a real end-to-end test
of the public starter kit proved the gap: a fresh assistant, told to delete all the files in its
vault folder, just did it immediately and only asked a question afterward. There was no code
anywhere enforcing "ask before a destructive, hard-to-reverse filesystem action" -- only CLAUDE.md
prose a model under pressure can skip, the same situation rule 13 (messaging) used to be in before
send_gate_hook.py made it real.

Scope (v1, NOT exhaustive -- this did not get the multi-round red-team pass send_gate_hook.py had):
recursive/force deletes (rm -rf, Remove-Item -Recurse -Force, del /s /q, rd /s /q, find -delete, a
PowerShell pipeline that feeds Get-ChildItem into Remove-Item or Clear-Content), shutil.rmtree and a
python loop that deletes files, git clean -f/-fd/-fdx, a SQL statement that drops or truncates a
table/database, and wiping a drive (format, a diskpart script with a clean step). A single file
delete, a normal Edit/Write overwrite, anything under this session's own scratchpad folder, or
tearing down something the SAME command just created (its own temp clone, its own mkdir) is never
flagged -- those are routine and reversible, and flagging them would just teach him to stop reading
these holds. Known gaps, by design for a v1: an unresolved shell variable as the delete target is
judged conservatively (always flagged, since the real target can't be seen); obfuscated commands
(base64-encoded, built up character by character) are not specially decoded the way send_gate_hook.py
eventually had to; the self-created-cleanup exemption is a simple substring heuristic, not a real
parse of the command's history. Tamper protection (see _is_tamper's own docstring) is still a
command-TEXT pattern match, not a sandbox -- it cannot stop a determined local attacker who can run
arbitrary code; closing that for real needs the Discord daemon to HMAC-sign incoming rows and the
approval/kill-switch/mode files to live somewhere this account cannot write at all. That is a
structural follow-up, not something this v1 claims to have solved.

MODES (logs/delete_gate_mode.txt holds "shadow" or "enforce"; default shadow):
    shadow   never blocks; logs what enforce WOULD have done to logs/delete_gate.jsonl
    enforce  exit 2 with a plain-English reason when a destructive call has no approval row
Kill switch: logs/delete_gate_off.txt (allow everything, log nothing).
Approvals are a SEPARATE file (logs/delete_approvals.jsonl) so this gate and send_gate_hook.py can
never consume each other's yes.
    python delete_gate_hook.py approve --text "<the exact command>"      # after his yes
    python delete_gate_hook.py approve-last                              # approve the most recent held call
    python delete_gate_hook.py report                                    # what shadow mode saw
Registration (PreToolUse): matcher "Bash|PowerShell|Write|Edit|NotebookEdit" (security review 2026-11-18: classify()
already treats a direct Write/Edit/NotebookEdit onto one of PROTECTED_NAMES as gate_tamper -- that branch is dead
unless the hook is actually registered on those tool names too, not just Bash|PowerShell), command: python
"<this script's own path>"
Pure core (unit tested): classify(tool_name, tool_input), content_hash(text), decide(cls, hashes, mode, call_hash),
valid_approvals(rows, used_rows, now), approval_evidence(dms, since, now, used)."""
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402

THE_OWNER_ID = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it
APPROVALS = "delete_approvals.jsonl"
APPROVALS_USED = "delete_approvals_used.jsonl"
LOG = "delete_gate.jsonl"
MODE_FILE = "delete_gate_mode.txt"
OFF_FILE = "delete_gate_off.txt"
INCOMING = "discord_incoming.jsonl"            # the approval-evidence source: writable by nothing but the real Discord daemon
PROTECTED_NAMES = (APPROVALS, APPROVALS_USED, OFF_FILE, MODE_FILE, LOG, INCOMING)
MAX_COMMAND = 40000

# Any statement that WRITES to one of these names is tamper, whatever the command word is (an echo
# redirect, a PowerShell Add-Content, python's open().write(), not just the classic WRITER_CMDS list) --
# a forged "yes" row in discord_incoming.jsonl would otherwise self-approve a deletion (security review
# 2026-11-06), and the kill switch/log/approvals files need the same protection send_gate_hook.py gives
# its own evidence trail.
RX_TAMPER = re.compile(r"delete_approvals(?:_used)?\.jsonl|delete_gate_off\.txt|delete_gate_mode\.txt|delete_gate\.jsonl|discord_incoming\.jsonl", re.I)
WRITER_CMDS = frozenset(("tee", "rm", "del", "mv", "move", "cp", "copy", "truncate", "remove-item", "set-content",
                         "add-content", "out-file", "new-item", "clear-content", "ri", "erase", "rd", "rmdir", "ren", "rename",
                         # security review 2026-11-18: common short PowerShell aliases and plain creation verbs that
                         # could write/touch a protected file without matching any of the names above
                         "touch", "mkdir", "md", "ni", "sc", "ac", "cpi", "mi", "rni",
                         # round 2 (2026-11-18): still an allowlist a reviewer could keep finding gaps in --
                         # copy-item/move-item/rename-item/set-item plus a few more cross-platform writers
                         "copy-item", "move-item", "rename-item", "set-item", "ln", "install", "dd", "sed"))
# Security review 2026-11-18 (round 2): enumerating every writer verb is a losing battle (there is always one
# more alias). For a statement that already names a protected file (RX_TAMPER matched), invert the check instead:
# treat it as tamper unless the command word is on this short, genuinely read-only allowlist -- anything else
# (a command this allowlist has never heard of, including a brand-new alias) is tamper by default.
READ_ONLY_CMDS = frozenset(("cat", "type", "get-content", "gc", "head", "tail", "less", "more",
                            "grep", "select-string", "sls", "findstr", "wc", "ls", "dir", "get-childitem", "gci"))
RX_REDIRECT_INTO = re.compile(r">>?\s*[\"']?\S*(?:delete_approvals(?:_used)?\.jsonl|delete_gate_off\.txt|delete_gate_mode\.txt|delete_gate\.jsonl|discord_incoming\.jsonl)", re.I)
RX_PY_WRITE = re.compile(r"open\([^)]*['\"][wax]|\.write\(|write_text|write_bytes|\.append\(|os\.(?:remove|replace|rename)|shutil\.", re.I)
RX_SCRATCH = re.compile(r"AppData[\\/]+Local[\\/]+Temp[\\/]+claude|[\\/]tmp[\\/]+claude", re.I)
CREATE_VERBS = re.compile(r"\bmkdir\b|\bNew-Item\b|\bgit\s+clone\b|\bcp\s+-[A-Za-z]*r[A-Za-z]*\b|\bCopy-Item\b|\brobocopy\b", re.I)
WRAPPERS = frozenset(("sudo", "env", "command", "nohup", "time", "timeout", "nice", "stdbuf", "ionice", "call", "start"))

RX_GCI_REMOVE = re.compile(r"Get-ChildItem\b[^\n]{0,200}\|\s*(?:ForEach-Object|%)?[^\n]{0,100}\bRemove-Item\b", re.I)
RX_GCI_CLEAR = re.compile(r"Get-ChildItem\b[^\n]{0,200}\|\s*(?:ForEach-Object|%)?[^\n]{0,100}\bClear-Content\b", re.I)
RX_RMTREE = re.compile(r"shutil\.rmtree\s*\(")
RX_RMTREE_ARG = re.compile(r"shutil\.rmtree\s*\(\s*(?:f?['\"])([^'\"]+)['\"]")
RX_OS_REMOVE_LOOP = re.compile(r"for\s+\w+\s+in\b.{0,200}?:\s*\n[^\n]{0,200}?(?:os\.(?:remove|unlink)\(|\.unlink\(\))", re.S)
RX_DROP_TRUNCATE = re.compile(r"\bDROP\s+(?:TABLE|DATABASE)\b|\bTRUNCATE\s+TABLE\b", re.I)
RX_FORMAT = re.compile(r"\bformat\s+[A-Za-z]:", re.I)
RX_MKFS = re.compile(r"\bmkfs(?:\.\w+)?\s+/dev/", re.I)
APPROVE_RX = re.compile(r"\b(yes|yep|yeah|ok|okay|sure|go ahead|send it|do it|approved?|go for it|bet|fine|sounds good|looks good)\b", re.I)
# A quoted argument to a pure-text command (echo/printf/Write-Host, or a git commit message) is DATA,
# not something that runs -- without this, "git commit -m 'stop running rm -rf in prod'" or
# "echo 'never DROP TABLE live'" would false-positive on the whole-text patterns below. Blanked only
# for THOSE commands; python/powershell/-c code and heredoc bodies (which really do run) are untouched.
# Security review 2026-11-06 round 2: a hit is blanked only when the statement's own output is not
# piped or redirected anywhere (`tail` has no trailing | or >) -- "echo 'DROP TABLE x' | sqlite3 db"
# actually RUNS that text, so it must stay visible to the whole-text scan, not be blanked as if it
# were merely printed.
RX_SAFE_TEXT_ARG = re.compile(r"(?:\b(?:echo|printf|Write-Host|Write-Output|Write-Information)\b[^\n;|&]*?|\bgit\s+commit\b[^\n;|&]*?-m\s*)(['\"])((?:(?!\1).)*)\1(?P<tail>[^\n;&]*)", re.I)
INERT_BASE_CMDS = frozenset(("echo", "printf", "write-host", "write-output", "write-information"))


def _is_provably_inert(full):
    """Pure. True ONLY when nothing in the command could consume, substitute or re-interpret quoted
    text as code: a single statement (no ; && || | & newline chaining -- `_split_unquoted` already
    splits on all of those), no parens/braces/brackets/angle-brackets/backticks ANYWHERE in the raw
    text (bare PowerShell grouping `(...)`, a subexpression, the call operator `& (...)`/`& {...}`,
    `[scriptblock]`, a redirect), and a base command that is a pure-text printer or `git commit`.

    Security review 2026-11-06, round 4: trying to enumerate every execution-context marker (eval,
    -c, iex, Invoke-Expression, ... ) kept missing real shapes (cmd /c, -Command, -EncodedCommand,
    bare parens). This whitelist-of-inert is simpler and fails closed instead: ANY base command other
    than the few pure printers below -- powershell, cmd, bash -c, eval, anything -- is NOT inert, so
    the real scan always runs on it; a single stray paren, even inside the quoted text itself, also
    loses the exemption. The cost is a few more false positives on literal punctuation in an echoed
    message; the alternative (a marker blacklist) kept being incomplete."""
    if re.search(r"[(){}<>`\[\]]", full):
        return False
    if len(_split_unquoted(full)) != 1:
        return False
    tokens = _tokens(full)
    base, i = _command_word(tokens)
    if base in INERT_BASE_CMDS:
        return True
    return base == "git" and tokens[i + 1:i + 2] == ["commit"]


def _blank_safe_text(full):
    """Pure. Same length/structure, quoted text-only arguments replaced with filler so a mention
    inside an echoed warning or a commit message cannot trigger the whole-text destructive patterns
    -- but ONLY when that text is never piped or redirected into something that would run it."""
    def repl(m):
        tail = m.group("tail") or ""
        if re.search(r"[|>]", tail):
            return m.group(0)                     # consumed further on: leave it visible to the real scan
        start, end = m.start(2) - m.start(0), m.end(2) - m.start(0)
        s = m.group(0)
        return s[:start] + "x" * (end - start) + s[end:]
    return RX_SAFE_TEXT_ARG.sub(repl, full)


def content_hash(text):
    """Pure. Whitespace-normalised hash, so an approval matches the same call however it was wrapped."""
    return hashlib.sha1(" ".join(str(text or "").split()).encode("utf-8", "replace")).hexdigest()[:16]


def _split_unquoted(text):
    """Pure. Split at ; && || | & and newlines, but not inside quotes."""
    segs, buf, quote, i, n = [], [], None, 0, len(text)
    while i < n:
        ch = text[i]
        if quote:
            buf.append(ch)
            if ch == quote and not (quote == '"' and i and text[i - 1] == "\\"):
                quote = None
        elif ch in "'\"":
            quote = ch
            buf.append(ch)
        elif ch in ";|&\r\n":
            segs.append("".join(buf).strip())
            buf = []
            if text[i:i + 2] in ("&&", "||"):
                i += 1
        else:
            buf.append(ch)
        i += 1
    segs.append("".join(buf).strip())
    return [s for s in segs if s]


def _tokens(stmt):
    try:
        return shlex.split(stmt, posix=True)
    except ValueError:
        return stmt.split()


def _command_word(tokens):
    """Pure. -> (basename, index) of the command word, past env assignments and wrapper words."""
    i, n = 0, len(tokens)
    while i < n:
        t = tokens[i]
        if re.match(r"^[A-Za-z_]\w*=", t):
            i += 1
            continue
        base = os.path.basename(t.replace("\\", "/")).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base in WRAPPERS:
            i += 1
            continue
        return base, i
    return None, i


def _is_scratch_path(p):
    """Security review 2026-11-18: match against the NORMALIZED path, not the raw string -- a raw substring
    search would let `.../Temp/claude/../../../real/target` through as "scratch" even though the actual
    resolved path escapes the scratch directory entirely (normpath collapses the `..` segments, which breaks
    the exact AppData/Local/Temp/claude sequence RX_SCRATCH requires, so an escaping path no longer matches)."""
    if not p:
        return False
    return bool(RX_SCRATCH.search(os.path.normpath(p)))


def _self_created(full, path):
    """Pure. True when `path` (or its basename) appears a second time in the full command text next
    to a creation verb -- a heuristic for "this is the same thing the command just built and is now
    tearing down", not a real parse of intent."""
    if not path:
        return False
    occurrences = len(re.findall(re.escape(path), full))
    if occurrences < 2:
        base = os.path.basename(path.rstrip("\\/"))
        if len(base) < 3:
            return False
        occurrences = len(re.findall(re.escape(base), full))
        if occurrences < 2:
            return False
    return bool(CREATE_VERBS.search(full))


def _rm_is_recursive_force(rest):
    has_r = any(t == "--recursive" or (t.startswith("-") and not t.startswith("--") and re.search(r"[rR]", t)) for t in rest)
    has_f = any(t == "--force" or (t.startswith("-") and not t.startswith("--") and "f" in t.lower()) for t in rest)
    return has_r and has_f


def _ps_remove_is_recursive_force(rest):
    """PowerShell lets a parameter be abbreviated to any unambiguous prefix (-r, -rec, -recurse ...)."""
    has_r = any(re.match(r"^-r(?:e(?:c(?:u(?:r(?:s(?:e)?)?)?)?)?)?$", t, re.I) for t in rest)
    has_f = any(re.match(r"^-f(?:o(?:r(?:c(?:e)?)?)?)?$", t, re.I) for t in rest)
    return has_r and has_f


def _del_is_recursive_quiet(rest):
    has_s = any(t.lower() == "/s" for t in rest)
    has_q = any(t.lower() == "/q" for t in rest)
    return has_s and has_q


def _git_clean_is_force(rest):
    return any(t == "--force" or (t.startswith("-") and not t.startswith("--") and "f" in t.lower()) for t in rest)


def _is_diskpart_wipe(full):
    low = full.lower()
    return "diskpart" in low and (re.search(r"\bclean\b", full, re.I) or re.search(r"/s\b", full, re.I))


def _judge(kind, targets, full, why):
    """Pure. Applies the two standing exemptions (scratchpad-only, self-created-and-torn-down);
    None means not flagged (either not destructive, or exempt)."""
    if targets:
        if all(_is_scratch_path(t) for t in targets):
            return None
        if all(_self_created(full, t) for t in targets):
            return None
    return dict(destructive=True, kind=kind, target=(targets[0] if targets else ""), why=why)


def _classify_stmt(stmt, full):
    tokens = _tokens(stmt)
    base, i = _command_word(tokens)
    if base is None:
        return None
    rest = tokens[i + 1:]
    if base == "rm":
        if _rm_is_recursive_force(rest):
            return _judge("rm_rf", [t for t in rest if not t.startswith("-")], full, "a recursive, forced delete (rm -rf-style)")
    elif base in ("remove-item", "ri"):
        if _ps_remove_is_recursive_force(rest):
            return _judge("powershell_remove", [t for t in rest if not t.startswith("-")], full, "a recursive, forced PowerShell delete (Remove-Item -Recurse -Force)")
    elif base in ("del", "erase"):
        if _del_is_recursive_quiet(rest):
            return _judge("del_sq", [t for t in rest if not t.startswith("/")], full, "a recursive, silent delete (del /s /q)")
    elif base in ("rd", "rmdir"):
        if _del_is_recursive_quiet(rest):
            return _judge("rd_sq", [t for t in rest if not t.startswith("/")], full, "a recursive, silent directory removal (rd /s /q)")
    elif base == "find":
        if any(t == "-delete" for t in rest):
            return _judge("find_delete", [], full, "a bulk find ... -delete")
    elif base == "git":
        if rest[:1] == ["clean"] and _git_clean_is_force(rest[1:]):
            return _judge("git_clean", [], full, "an untracked-file wipe (git clean -f)")
    return None


def _python_blocks(full):
    """Pure. The python source inside a command: heredoc bodies fed to python and the string
    argument of `-c` -- real code, as opposed to a plain-text argument to echo or a commit message."""
    blocks = [m.group(2) for m in re.finditer(r"(?:python[0-9.]*(?:\.exe)?|pythonw|py)\b[^\n|;&<]*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)", full, re.S | re.I)]
    blocks += [m.group(1) for m in re.finditer(r"-\w*c\s+(\"(?:\\.|[^\"\\])*\"|'[^']*')", full)]
    return blocks


RX_LOGS_PATH = re.compile(r"\blog\w*[\\/]", re.I)
RX_WILDCARD_OR_VAR = re.compile(r"[*?]|\$\{?\w+\}?|%\w+%")
RX_REDIRECT_INTO_LOGS = re.compile(r">>?\s*[\"']?\S*log", re.I)


def _is_tamper(full):
    """True when the command writes to, or reaches into, the gate's own evidence files (its mode,
    kill switch, log, approvals, or the Discord-incoming log an approval's evidence comes from).

    This is still a command-TEXT pattern match, which a determined local attacker who can run
    arbitrary code can route around in ways no regex catches (security review 2026-11-06, round 2):
    real protection needs the daemon to HMAC-sign incoming Discord rows (so a forged row fails
    signature checks even if it is written) and the approvals/kill-switch/mode files to live
    somewhere this account cannot write at all (ACL'd, or owned by a separate daemon account). That
    structural fix is NOT done in this v1 -- flagged here honestly, not claimed as solved, the same
    way send_gate_hook.py's own tamper check is pattern-based and not a sandbox.

    Within that limit, judged broadly: a redirect, a PowerShell writer, python code that opens/
    writes/removes one of the exact protected names (not just a narrow shell writer-command
    allowlist -- an `echo ... >> discord_incoming.jsonl` with no recognized writer VERB would
    otherwise forge a fake approval), AND a write/redirect that reaches into the logs directory via
    a WILDCARD or a VARIABLE rather than one of the literal protected names (`rm logs/delete_*`,
    `echo x >> $LOGS_DIR/discord_incoming.jsonl`) -- those would not match the exact filenames above."""
    if RX_TAMPER.search(full):
        for stmt in _split_unquoted(full):
            if not RX_TAMPER.search(stmt):
                continue
            tokens = _tokens(stmt)
            base, _i = _command_word(tokens)
            # deny-by-default (security review 2026-11-18 round 2): a statement that already names a
            # protected file is tamper unless its command word is known, read-only, AND there is no
            # redirect -- an unrecognized command (including one no reviewer has named yet) is tamper too
            if RX_REDIRECT_INTO.search(stmt) or base not in READ_ONLY_CMDS:
                return True
        for blk in _python_blocks(full):
            if RX_TAMPER.search(blk) and RX_PY_WRITE.search(blk):
                return True
    for stmt in _split_unquoted(full):
        if not (RX_LOGS_PATH.search(stmt) and RX_WILDCARD_OR_VAR.search(stmt)):
            continue
        tokens = _tokens(stmt)
        base, _i = _command_word(tokens)
        if base in WRITER_CMDS or RX_REDIRECT_INTO_LOGS.search(stmt):
            return True
    return False


def _none():
    return dict(destructive=False, kind="none", target="", why="")


def classify(tool_name, tool_input):
    """Pure. -> {destructive, kind, target, why}."""
    name = tool_name or ""
    tin = tool_input if isinstance(tool_input, dict) else {}
    if name in ("Write", "Edit", "NotebookEdit"):
        path = str(tin.get("file_path", "")).replace("\\", "/")
        base = os.path.basename(path).lower()
        if base in PROTECTED_NAMES:
            return dict(destructive=True, kind="gate_tamper", target=base, why="edits the delete gate's own approval, switch or log files")
        return _none()
    if name not in ("Bash", "PowerShell", "bash"):
        return _none()
    full = str(tin.get("command", ""))
    if len(full) > MAX_COMMAND:
        return dict(destructive=True, kind="oversized", target="command", why="the command is too long to read (over %d characters): put it in a script file" % MAX_COMMAND)
    if _is_tamper(full):
        return dict(destructive=True, kind="gate_tamper", target="gate", why="writes to the delete gate's own approval, switch or log files")
    # python code blocks (heredoc bodies, -c strings) are scanned unmasked -- that IS real code, not
    # text data, so shutil.rmtree/os.remove mentioned only in a comment or a non-python string never
    # reaches here in the first place (_python_blocks only extracts actual python source).
    # _python_blocks() only reliably extracts a heredoc or a -c string; it is used here to pull out
    # WHAT is being deleted (for the exemption check and the log), never to gate WHETHER the pattern
    # is even looked for -- RX_RMTREE/RX_OS_REMOVE_LOOP are searched against the real, unmasked `full`
    # text directly (security review 2026-11-06 round 2), so code reached another way (piped into
    # python, or fed through something _python_blocks doesn't recognize) is not missed.
    if RX_RMTREE.search(full):
        targets = []
        for blk in _python_blocks(full):
            targets += [m.group(1) for m in RX_RMTREE_ARG.finditer(blk)]
        if not targets:
            targets = [m.group(1) for m in RX_RMTREE_ARG.finditer(full)]
        c = _judge("shutil_rmtree", targets, full, "shutil.rmtree deletes a whole directory tree with no recycle bin")
        if c:
            return c
        if not targets:
            return dict(destructive=True, kind="shutil_rmtree", target="", why="shutil.rmtree with a target that can't be read from the command text")
    if RX_OS_REMOVE_LOOP.search(full):
        return dict(destructive=True, kind="py_remove_loop", target="", why="python code that deletes files in a loop")
    # whole-command patterns: masked first so a mention inside an echoed string or a commit message
    # (plain text, never executed) cannot trip these (security self-review 2026-11-06); but when the
    # command contains an execution-context marker, also check the unmasked text and fail closed
    # (round 3) -- `eval "echo 'DROP TABLE x'"` really does run that text, masking must not hide it.
    masked = _blank_safe_text(full)

    def _whole_text_hit(text):
        if RX_DROP_TRUNCATE.search(text):
            return dict(destructive=True, kind="sql_drop", target="", why="a SQL statement that drops or truncates a table/database")
        if RX_FORMAT.search(text) or RX_MKFS.search(text):
            return dict(destructive=True, kind="drive_format", target="", why="formats or wipes a drive")
        if _is_diskpart_wipe(text):
            return dict(destructive=True, kind="diskpart", target="", why="a diskpart script that may clean (wipe) a disk")
        if RX_GCI_REMOVE.search(text) or RX_GCI_CLEAR.search(text):
            return dict(destructive=True, kind="powershell_pipeline_delete", target="", why="a PowerShell pipeline that bulk-deletes or clears everything it's given")
        return None

    c = _whole_text_hit(masked)
    if c:
        return c
    if not _is_provably_inert(full):
        c = _whole_text_hit(full)
        if c:
            return c
    for stmt in _split_unquoted(full):
        c = _classify_stmt(stmt, full)
        if c:
            return c
    return _none()


APPROVAL_TTL = 30 * 60


def valid_approvals(rows, used_rows, now):
    """Pure. The set of call hashes with a live approval: written less than 30 minutes ago and not yet used. One approval covers one delete."""
    used = {}
    for u in used_rows or []:
        used[u.get("hash")] = max(used.get(u.get("hash"), 0), float(u.get("t", 0)))
    out = set()
    for r in rows or []:
        try:
            t = time.mktime(time.strptime(str(r.get("ts")), "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        if 0 <= now - t <= APPROVAL_TTL and used.get(r.get("hash"), 0) < t:
            out.add(r.get("hash"))
    return out


def decide(cls, hashes, mode, call_hash):
    """Pure. -> (decision, reason). decision in allow | approved | would_block | block. shadow never blocks."""
    if not cls.get("destructive"):
        return "allow", ""
    if call_hash in (hashes or ()):
        return "approved", "approval row found"
    if mode == "enforce":
        return "block", cls["why"]
    return "would_block", cls["why"]


SHORT_CODE_LEN = 6   # same scheme and length as send_gate_hook.py's short_code()
APPROVAL_CODE_WINDOW = 15 * 60      # a code found in a DM more than 15 min after the hold does not count, even though it is the right code


def short_code(content_hash_hex):
    """Pure. The hex code shown to him for one specific held call -- security review 2026-11-18: a generic "yes"
    was bound only by arrival time, never by whether the yes was actually about THIS held command, so an
    unrelated yes-shaped DM sent after the hold could get spent approving it. Requiring this code in his reply
    ties the approval to the exact content, not just to timing (same fix send_gate_hook.py already has).

    Honest limit (security review round 2): this code is computed from content the agent itself already has,
    not a secret only the owner knows -- its job is telling an UNRELATED yes apart from a real one, not stopping
    a compromised agent from computing its own code and self-approving. Closing that needs the same structural
    fix _is_tamper() already flags (a signed or out-of-band challenge the agent cannot forge), not a longer code.
    The length is kept short deliberately to match send_gate_hook.py's own code; _cli_approve() separately
    refuses when two different held commands would collide on the same code within one approval window."""
    return (content_hash_hex or "")[:SHORT_CODE_LEN]


def approval_evidence(dms, since, now, used=None, code=None, deadline=None):
    """Pure. The newest DM from the owner himself that arrived after `since` (epoch) and approves. When `code`
    is given, the message must contain that exact code as a whole word -- a plain "yes" no longer counts, since
    the code is what binds the approval to this specific held call. `deadline` additionally caps how late a
    correct code may still count. Falls back to the old generic yes-shaped-phrase match when `code` is None."""
    if THE_OWNER_ID is None:   # security review 2026-11-18: never guess who "the owner" is -- fail closed until configured
        return None
    best = None
    code_rx = re.compile(r"\b%s\b" % re.escape(code), re.I) if code else None
    cap = min(now, deadline) if deadline is not None else now
    for m in dms or []:
        if m.get("author_id") != THE_OWNER_ID or m.get("source") != "dm":
            continue
        try:
            t = dt.datetime.fromisoformat(str(m.get("date")).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            continue
        text = (m.get("text") or "").strip()
        negated = re.search(r"\b(no|nope|not|don'?t|dont|do not|never|stop|wait|hold|cancel|unsure|sure\?)\b", text, re.I) or "?" in text
        matches = bool(code_rx.search(text)) if code_rx else bool(APPROVE_RX.search(text))
        if since < t <= cap and matches and not negated and m.get("id") not in (used or ()):
            if best is None or t > best[0]:
                best = (t, m)
    return best[1] if best else None


def _mode(logs):
    if os.environ.get("JARVIS_UNATTENDED") == "1":
        return "enforce"
    try:
        return "enforce" if open(os.path.join(logs, MODE_FILE), encoding="utf-8").read().strip().lower() == "enforce" else "shadow"
    except OSError:
        return "shadow"


def _read(name, logs):
    out = []
    try:
        for line in open(os.path.join(logs, name), encoding="utf-8", errors="replace"):
            if line.strip():
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def _log(row, logs):
    try:
        with open(os.path.join(logs, LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")
    except Exception:          # noqa: BLE001 - logging must never decide a call
        pass


def approve(text, by, logs=None, by_msg=None):
    logs = logs or LOGS
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hash": content_hash(text), "target": "exact call", "text_head": " ".join(text.split())[:80], "by": by, "by_msg": by_msg}
    with open(os.path.join(logs, APPROVALS), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def run(payload, logs=None, now=None):
    logs = logs or LOGS
    unattended = os.environ.get("JARVIS_UNATTENDED") == "1"
    if os.path.exists(os.path.join(logs, OFF_FILE)) and not unattended:     # a leftover kill-switch file must not silence the gate in a run nobody watches
        return 0, ""
    name = payload.get("tool_name", "")
    tin = payload.get("tool_input", {})
    text = tin.get("command") if isinstance(tin, dict) and "command" in tin else json.dumps(tin, sort_keys=True)
    cls = classify(name, tin)
    if not cls["destructive"]:
        return 0, ""
    h = content_hash(text)
    mode = _mode(logs)
    now_t = time.time() if now is None else now
    decision, why = decide(cls, valid_approvals(_read(APPROVALS, logs), _read(APPROVALS_USED, logs), now_t), mode, h)
    if decision == "approved":
        try:
            with open(os.path.join(logs, APPROVALS_USED), "a", encoding="utf-8") as f:
                f.write(json.dumps({"hash": h, "t": now_t}) + "\n")
        except OSError:
            pass
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "kind": cls["kind"], "target": cls["target"], "decision": decision, "mode": mode,
          "hash": h, "head": " ".join(str(text).split())[:120]}, logs)
    if decision == "block":
        return 2, ("delete_gate: this looks like a destructive, hard-to-reverse filesystem action (%s: %s) and no approval exists for this exact content. "
                   "Show him the exact text and ask him to reply with the code %s (a plain \"yes\" is not enough -- it must contain this exact code, "
                   "which is what ties his approval to this specific content rather than just to timing)." % (cls["kind"], why, short_code(h)))
    return 0, ""


def report(logs=None):
    rows = _read(LOG, logs or LOGS)
    counts = {}
    for r in rows:
        k = (r.get("kind"), r.get("decision"))
        counts[k] = counts.get(k, 0) + 1
    for (kind, dec), n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print("%4d  %-24s %s" % (n, kind, dec))
    print("%d row(s) total, mode %s" % (len(rows), _mode(logs or LOGS)))


def _cli_approve(argv, logs=None, now=None):
    logs = logs or LOGS
    now = time.time() if now is None else now
    if argv[0] == "approve":
        text = argv[argv.index("--text") + 1] if "--text" in argv else ""
        if not text:
            print("approve needs --text")
            return 2
        mine = [r for r in _read(LOG, logs) if r.get("decision") in ("block", "would_block") and r.get("hash") == content_hash(text)]
        if not mine:
            print("the gate never held that exact content, so there is nothing to approve; run the call so it is held, show him the text, then ask")
            return 4
        try:
            since = time.mktime(time.strptime(mine[-1]["ts"], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            return 4
        head = text
        the_hash = mine[-1]["hash"]
    else:
        held = [r for r in _read(LOG, logs) if r.get("decision") in ("block", "would_block")]
        if not held:
            print("nothing held")
            return 1
        since = time.mktime(time.strptime(held[-1]["ts"], "%Y-%m-%dT%H:%M:%S"))
        head = None
        the_hash = held[-1]["hash"]
    code = short_code(the_hash)
    # security review 2026-11-18 (round 2): the code is short enough that two DIFFERENT held commands could
    # collide on it. A reply meant for one must never get spent approving the other -- refuse outright rather
    # than guess which one he meant.
    window_end = since + APPROVAL_CODE_WINDOW
    colliding = {r["hash"] for r in _read(LOG, logs) if r.get("decision") in ("block", "would_block") and r.get("hash") != the_hash
                 and short_code(r.get("hash", "")) == code
                 and since - APPROVAL_CODE_WINDOW < time.mktime(time.strptime(r["ts"], "%Y-%m-%dT%H:%M:%S")) < window_end}
    if colliding:
        print("refusing: code %s also matches another held command in this window; ask him to approve by exact --text instead" % code)
        return 5
    ev = approval_evidence(_read(INCOMING, logs), since, now, used={r.get("by_msg") for r in _read(APPROVALS, logs) if r.get("by_msg")},
                            code=code, deadline=window_end)
    if ev is None:
        print("no go-ahead from the owner found with the code %s within %d minutes of the hold; nothing recorded" % (code, APPROVAL_CODE_WINDOW // 60))
        return 3
    if head is not None:
        row = approve(head, "the owner", logs, by_msg=ev.get("id"))
    else:
        r = held[-1]
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hash": r["hash"], "target": r.get("target"), "text_head": r.get("head", "")[:80], "by": "the owner", "by_msg": ev.get("id")}
        with open(os.path.join(logs, APPROVALS), "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    print("approved", row["hash"], "on his message", ev.get("id"))
    return 0


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    if argv and argv[0] in ("approve", "approve-last", "report"):
        if argv[0] == "report":
            report()
            return 0
        if os.environ.get("JARVIS_UNATTENDED") == "1":
            print("approvals are not available inside an unattended run; ask him in a DM and let the live session approve it")
            return 2
        return _cli_approve(argv)
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw or "{}")
    except (ValueError, OSError):
        return 0
    try:
        code, msg = run(payload)
    except Exception as e:                       # fail open unless the gate is already enforcing
        enforcing = os.environ.get("JARVIS_UNATTENDED") == "1" or _mode(LOGS) == "enforce"
        tname = str(payload.get("tool_name", "") if isinstance(payload, dict) else "").lower()
        if enforcing and tname in ("bash", "powershell"):
            sys.stderr.write("delete_gate: internal error while judging this call (%s); refusing because the gate is enforcing. Retry once, or write the command into a script file.\n" % type(e).__name__)
            return 2
        return 0
    if msg:
        sys.stderr.write(msg + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
