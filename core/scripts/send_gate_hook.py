# -*- coding: utf-8 -*-
"""PreToolUse hook: rule 13 as code (2026-10-30, step C3 of the harness redo).

Rule 13 says nothing another person would see or receive goes out without the owner's go-ahead for that exact content. Until now that was prose, and the
audit that night found no hook enforced it. This reads every Bash, PowerShell and MCP call, decides whether it sends something to anyone other than him
(a Discord send aimed at a friend or channel, a Telegram send, mail, a web POST, python code that posts, a pull request or push), and checks logs/approvals.jsonl
for a row whose hash matches that exact call.
Standing exceptions written in his messaging rules are allowed and logged, not blocked: a message to the owner themself, a reply (--reply-to) inside the
group channel, and a reply to a close friend's DM. Anything else external needs an approval row.
An approval row can only be written by `approve` / `approve-last`, and those refuse unless the owner's own DM (read from the Discord log, which the daemon writes)
arrived after the hold and says yes; the row records that message id. Editing approvals.jsonl or the gate's switch files from a shell is itself a gated call.
MODES (logs/send_gate_mode.txt holds "shadow" or "enforce"; the default is shadow):
    shadow   never blocks; writes every external call and what enforce WOULD have done to logs/send_gate.jsonl, so the rules can be tuned on real traffic
    enforce  exit 2 with a plain-English reason when an external call has no approval row
Kill switch: logs/send_gate_off.txt (allow everything, log nothing). Fails open on a bad payload or any error in a call that does not look like a send, and fails
closed (in enforce) when the call does look like one. The hook also records every call signature for the budget breaker (identical-call counter).
    python send_gate_hook.py approve --text "<the exact command>"      # after his yes: record the go-ahead for exactly this call
    python send_gate_hook.py approve-last                              # after his yes: approve the most recent call that was held
    python send_gate_hook.py report                                    # what shadow mode saw: counts by kind and decision
Registration (PreToolUse): matcher "Bash|PowerShell|mcp__.*", command: python "J:\\Jarvis2\\scripts\\send_gate_hook.py"
Pure core (unit tested): classify(tool_name, tool_input, lookup), content_hash(text), decide(cls, hashes, mode, call_hash), approval_evidence(dms, since, now)."""
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402  (same pack: config parsing, owner_id reader)
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")


def _owner_id():
    """The owner's Discord user id from logs/discord_config.json (same source as discord_daemon.py uses), or None
    when not configured yet. Read fresh each call rather than cached at import time, since this hook starts a new
    process every call anyway and the owner may only just have filled in the config."""
    return dqs.owner_id(LOGS)
APPROVALS = "approvals.jsonl"
LOG = "send_gate.jsonl"
MODE_FILE = "send_gate_mode.txt"
OFF_FILE = "send_gate_off.txt"

# the script may be started with python in front, or directly (a bare name, a path, a quoted path); a name that merely ends in it (test_discord_queue_send.py) does not count
SCRIPT_START = r"(?:^|[\s/\\\"'])"
RX_DISCORD = re.compile(SCRIPT_START + r"discord_queue_send\.py", re.I)
RX_DISCORD_MOD = re.compile(r"\bpython[0-9.]*(?:\.exe)?\s+(?:-\w+\s+)*-m\s+discord_queue_send\b", re.I)
RX_TELEGRAM = re.compile(SCRIPT_START + r"telegram_(?:bot_send|queue_send|send)\.py", re.I)
# python code that sends: found by the red-team run of 2026-10-30 (a free worker wrote 132 tricky commands; 19 slipped through, most of them python one-liners and heredocs)
RX_PY_CODE = re.compile(r"(?<![\w.])(?:python[0-9.]*(?:\.exe)?|pythonw|py)\b[^|;&\n]*?\s(?:-\w*c\b|-\s*<<)|(?<![\w.])(?:python[0-9.]*(?:\.exe)?|pythonw|py)\s*<<", re.I)
CODE_SEND = re.compile(r"requests\.(?:post|put|patch|delete)\s*\(|httpx\.(?:post|put|patch|delete)\s*\(|urlopen\s*\([^)]*\bdata\s*=|\bsmtplib\b|\.sendmail\s*\(|\bhttp\.client\b"
                       r"|['\"]curl['\"][^\n]{0,120}(?:POST|PUT|PATCH|DELETE|--data|['\"]-d['\"])"
                       r"|\.request\s*\(\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]|getattr\s*\(\s*\w+\s*,\s*['\"](?:post|put|patch|delete|request|send\w*)['\"]"
                       r"|importlib\.import_module\s*\(\s*['\"](?:requests|httpx|smtplib|http\.client|urllib3)|['\"][^'\"\n]*(?:discord_queue_send|telegram_\w*send)[^'\"\n]*['\"][\s\S]{0,200}?['\"]--(?:channel|user|reply-to)"
                       r"|\.(?:post|put|patch|delete)\s*\(\s*f?['\"]https?://|__import__\s*\(\s*['\"](?:requests|httpx|smtplib|http\.client)"
                       r"|(?:import|from)\s+discord_queue_send\b|discord_queue_send\.\w*send\w*\s*\(|subprocess\.\w+\s*\(\s*\[\s*['\"](?:git|gh|scp|rsync|sftp)['\"][^\]]{0,120}['\"](?:push|upload|create|comment)['\"]"
                       r"|requests\.Session\s*\(|\bRequest\s*\([^)]*\bdata\s*=|os\.system\s*\(\s*['\"][^'\"]*curl[^'\"]*(?:-X\s*(?:POST|PUT|PATCH|DELETE)|--data|\s-d\b)", re.I)
RX_MAIL = re.compile(r"(?:\bsmtplib\b|\bsendmail\b|\bSend-MailMessage\b|\bmail_send\b|gmail[^|;&\n]*\bsend\b)", re.I)
RX_IWR_POST = re.compile(r"\b(?:Invoke-WebRequest|Invoke-RestMethod|iwr|irm|curl)\b[^|;&\n]*(?:-Method\s+(?:Post|Put|Patch|Delete)|-(?:Body|InFile)\b)|\.Upload(?:String|Data|File)\s*\(", re.I)
RX_PIPE_SHELL = re.compile(r"\|\s*(?:ba|z|da)?sh(?:\.exe)?\b|\|\s*(?:powershell|pwsh|cmd)(?:\.exe)?\b", re.I)      # kept for old callers; classify() uses _pipes_into_interpreter
RX_FETCH_RUN = re.compile(r"\b(?:ba|z|da|k)?sh\b[^\n|;&]{0,80}<\(\s*(?:curl|wget)|\b(?:ba|z|da)?sh\s+-c\s+[\"']?\$\(\s*(?:curl|wget)|\bsource\s+<\(\s*(?:curl|wget)"
                          r"|\b(?:iex|Invoke-Expression)\b[^\n]{0,200}\b(?:iwr|irm|Invoke-WebRequest|Invoke-RestMethod|DownloadString)\b", re.I)
RX_OUTBOX = re.compile(r"(?:discord|telegram)\w*outbox", re.I)
RX_OUTBOX_REDIRECT = re.compile(r">>?\s*[\"']?\S*(?:discord|telegram)\w*outbox", re.I)
SHELL_NAMES = frozenset(("sh", "bash", "zsh", "dash", "ksh", "fish", "pwsh", "powershell", "cmd", "iex", "invoke-expression"))
STDIN_INTERPRETERS = frozenset(("python", "python3", "py", "pythonw", "node", "perl", "ruby", "php"))
WRAPPERS = frozenset(("timeout", "nice", "sudo", "env", "nohup", "time", "command", "exec", "stdbuf", "ionice", "xargs", "setsid", "start", "call"))
WRITER_CMDS = frozenset(("tee", "rm", "del", "mv", "move", "cp", "copy", "touch", "truncate", "remove-item", "set-content", "add-content", "out-file", "new-item", "clear-content",
                         "ri", "erase", "rd", "rmdir", "ren", "rename", "xcopy", "robocopy"))
CODE_PROTECTED_NAMES = ("send_gate_hook.py", "budget.py", "loop_lock.py", "quarantine.py", "integrity_check.py", "settings.json", "settings.local.json")
RX_TAMPER_CODE = re.compile(r"(?:send_gate_hook|budget|loop_lock|quarantine|integrity_check)\.py|settings(?:\.local)?\.json", re.I)
MAX_COMMAND = 40000
RX_TAMPER = re.compile(r"approvals(?:_used)?\.jsonl|send_gate_off\.txt|send_gate_mode\.txt|send_gate\.jsonl|discord_incoming\.jsonl", re.I)
PROTECTED_NAMES = ("approvals.jsonl", "approvals_used.jsonl", "send_gate_off.txt", "send_gate_mode.txt", "send_gate.jsonl", "discord_incoming.jsonl")
RX_PY_WRITE = re.compile(r"open\([^)]*['\"][wax]|\.write\(|write_text|write_bytes|\.append\(|os\.(?:remove|replace|rename)|shutil\.", re.I)
RX_GATE_IMPORT = re.compile(r"send_gate_hook[^\n]{0,80}\bapprove\(|from\s+send_gate_hook\s+import", re.I)
RX_WRITER = re.compile(r"\btee\b|\brm\b|\bdel\b|\bmv\b|\bmove\b|\bcp\b|\bcopy\b|\btouch\b|\btruncate\b|\bsed\s+-i|Remove-Item|Set-Content|Add-Content|Out-File|New-Item|Clear-Content", re.I)
RX_REDIRECT_INTO = re.compile(r">>?\s*[\"']?[^\s|;&\"'<>]*(?:approvals(?:_used)?\.jsonl|send_gate_off\.txt|send_gate_mode\.txt|send_gate\.jsonl|discord_incoming\.jsonl)", re.I)
LOCAL_HOST = re.compile(r"(?:localhost|127\.0\.0\.1|\[::1\])")
URL = re.compile(r"https?://[^\s'\"]+", re.I)
SEND_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
CURL_DATA = ("-d", "--data", "--json", "-F", "--form", "-T", "--upload-file")
GIT_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
GH_SENDS = {("pr", "create"), ("pr", "comment"), ("pr", "review"), ("pr", "merge"), ("pr", "edit"), ("pr", "close"), ("issue", "create"), ("issue", "comment"),
            ("issue", "edit"), ("issue", "close"), ("release", "create"), ("release", "upload"), ("release", "edit"), ("release", "delete"), ("gist", "create"), ("gist", "edit"), ("repo", "create"), ("repo", "delete")}
MCP_SKIP = ("claude_ai_claude_docs", "playwright", "windows-mcp", "blender", "context7", "roblox_studio", "plugin_canva")
MCP_READ_FIRST = {"list", "get", "search", "read", "fetch", "find", "query", "describe", "view", "show", "check", "count", "lookup"}
MCP_SEND_TOKENS = {"send", "post", "publish", "submit", "reply", "comment", "push", "merge", "upload", "tweet", "dm", "email", "notify"}
UNATTENDED_MUTATE = {"click", "type", "fill", "press", "evaluate", "run", "upload", "drag", "drop", "paste", "shortcut", "clipboard", "powershell", "registry", "process",
                     "filesystem", "multiedit", "multiselect", "app", "handle", "select", "key", "computer", "form", "javascript", "input"}      # browser and desktop tools that act, in a run nobody watches
# The four sets below are standing exceptions: groups/channels/hosts the owner has explicitly told the assistant it
# never needs to ask about again. This kit ships with none -- a real setup earns these over time from the owner's own
# words ("go autonomous for that chat", "this is my own machine, stop asking me"), never guessed or assumed. Add your
# own real ids/hostnames here only after the owner says so explicitly, and keep host matching EXACT (never substring:
# a substring match on a hostname would also pass "yourhost.evil.com").
STANDING_CHANNELS = frozenset()   # Discord channel ids the owner has approved for autonomous posting
TELEGRAM_STANDING_CHATS = frozenset()    # Telegram chat ids (a group, or the owner's own DM chat_id) approved the same way
RIG_COMPUTE_HOSTS = frozenset()   # hostnames/ips of the owner's own machine(s) that ssh/scp/rsync may reach without asking each time (exact match only)
NITE_HOSTS = frozenset()   # same idea as RIG_COMPUTE_HOSTS, for a second machine if the owner has one (exact match only)
RX_DECODE_EXEC = re.compile(r"(?:\beval\b|\|\s*(?:ba|z|da)?sh\b|\|\s*(?:python[0-9.]*|pwsh|powershell)\b|\bsource\s+<\()[^\n]{0,300}\bbase64\s+(?:-d|-D|--decode)\b"
                            r"|\bbase64\s+(?:-d|-D|--decode)\b[^\n|]{0,300}\|\s*(?:ba|z|da)?sh\b|\b(?:eval|bash\s+-c|sh\s+-c)\s+[\"']?\$\([^\n)]{0,300}\bbase64\s+(?:-d|-D|--decode)\b", re.I)
RX_PY_SPAWN_CLAUDE = re.compile(r"subprocess\.\w+\s*\(\s*\[[^\]]{0,300}claude(?:\.exe)?['\"][^\]]{0,300}['\"](?:-p|--print)['\"]", re.I)
MCP_SEND_COMPOUND = ("pull_request", "create_issue", "create_comment", "create_message", "push_files", "create_gist")
TALKERS = frozenset(("echo", "printf", "cat", "grep", "rg", "egrep", "sed", "awk", "less", "more", "head", "tail", "ls", "type", "write-output", "write-host",
                     "select-string", "findstr", "wc", "diff", "test", "set-content", "add-content", "out-file"))
APPROVE_RX = re.compile(r"\b(yes|yep|yeah|ok|okay|sure|go ahead|send it|do it|approved?|go for it|bet|fine|sounds good|looks good)\b", re.I)


def content_hash(text):
    """Pure. Whitespace-normalised hash, so an approval matches the same call however it was wrapped. SHA-256 (Fable 5.2 review, 2026-11-16: "switch SHA-1 to
    SHA-256") -- the truncation to 16 hex chars already dominates the real collision math here, but there is no reason to keep a weaker primitive underneath."""
    return hashlib.sha256(" ".join(str(text or "").split()).encode("utf-8", "replace")).hexdigest()[:16]


RX_HEREDOC = re.compile(r"(?<!<)(<<(?!<)-?\s*['\"]?(\w+)['\"]?)([^\n]*)\n.*?\n[ \t]*\2[ \t]*(?=\n|$)", re.S)


RX_PY_HEREDOC = re.compile(r"(?<![\w.])(?:python[0-9.]*(?:\.exe)?|pythonw|py)\b[^\n|;&<]*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)", re.S | re.I)
RX_SHELL_HEREDOC = re.compile(r"(?:\b(?:ba|z|da)?sh(?:\.exe)?|\b(?:powershell|pwsh)(?:\.exe)?|\bcmd(?:\.exe)?)\b[^\n|;&<]*<<-?\s*['\"]?(\w+)['\"]?[^\n]*\n(.*?)\n[ \t]*\1[ \t]*(?=\n|$)", re.S | re.I)


def _is_writer(stmt):
    """True when the statement's command word writes, moves or deletes files (tee, rm, mv, cp, Set-Content, sed -i ...): the word has to be the COMMAND, so a commit message that
    says 'copy approvals.jsonl rows' is not a write."""
    toks = _tokens(stmt)
    i = _command_word_index(toks)
    if i < 0:
        return False
    base = os.path.basename(toks[i].replace("\\", "/")).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    if base in WRITER_CMDS or (base == "sed" and any(t.startswith("-i") or t == "--in-place" for t in toks[i + 1:])):
        return True
    if base in ("bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh", "eval"):          # the writer may be inside the quoted -c / -Command string
        inner = [toks[k + 1] for k in range(i + 1, len(toks) - 1) if re.match(r"^(-[A-Za-z]*c|-command|/c|/k)$", toks[k], re.I)] + ([" ".join(toks[i + 1:])] if base == "eval" else [])
        return any(_is_writer(s2) for txt in inner for s2 in _split(txt))
    return False


def _is_tamper(full, body, unattended=False):
    """True when the command writes to, or approves through, the gate's own files (and, in a run nobody watches, the gate's own code and settings). Judged per statement and per
    python block (not over the whole text), so a heredoc that is only WRITING A TEST which mentions these files, or a command that reads one with `2>&1`, is not tampering."""
    rx_t = re.compile(RX_TAMPER.pattern + "|" + RX_TAMPER_CODE.pattern, re.I) if unattended else RX_TAMPER
    rx_r = re.compile(RX_REDIRECT_INTO.pattern[:-1] + "|" + RX_TAMPER_CODE.pattern + ")", re.I) if unattended else RX_REDIRECT_INTO
    for stmt in _split(body):
        if rx_t.search(stmt) and (_is_writer(stmt) or rx_r.search(stmt)):
            return True
        if RX_PY_CODE.search(stmt) and ((rx_t.search(stmt) and RX_PY_WRITE.search(stmt)) or RX_GATE_IMPORT.search(stmt)):
            return True
    for m in RX_PY_HEREDOC.finditer(full or ""):
        blk = m.group(2)
        if (rx_t.search(blk) and RX_PY_WRITE.search(blk)) or RX_GATE_IMPORT.search(blk):
            return True
    return False


def _shell_heredoc_bodies(cmd):
    """Pure. The bodies of heredocs fed to a shell (bash <<EOF ... EOF): those are scripts that will run, unlike the body of `cat <<EOF`."""
    return [m.group(2) for m in RX_SHELL_HEREDOC.finditer(cmd or "")]


def _quoted_at(cmd, pos):
    """Pure. True when position `pos` of the shell text is inside a quoted string (comments are skipped, so an apostrophe in a comment opens nothing)."""
    q, i = None, 0
    while i < pos:
        c = cmd[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or cmd[i - 1] in " \t\n;&|("):
            while i < pos and cmd[i] != "\n":
                i += 1
            continue
        i += 1
    return q is not None


def _strip_heredocs(cmd):
    """Pure. Heredoc BODIES are text being written, not commands, so they are dropped; the marker and the rest of its line stay (that line can hold a pipe into a send).
    A `<<` inside quotes, or the here-string `<<<`, is not a heredoc: stripping it would delete a real command line (third review 2026-10-30)."""
    cmd = cmd or ""
    return RX_HEREDOC.sub(lambda m: m.group(0) if _quoted_at(cmd, m.start()) else m.group(1) + m.group(3), cmd)


def _split(cmd):
    """Pure. Split a shell line into statements at ; | & and newlines, but only outside quotes, so a message text containing a semicolon does not hide a --user flag."""
    out, cur, q, i, n = [], [], None, 0, len(cmd)
    while i < n:
        c = cmd[i]
        if q:
            cur.append(c)
            if c == "\\" and q == '"' and i + 1 < n:
                cur.append(cmd[i + 1])
                i += 1
            elif c == q:
                q = None
        elif c in "'\"":
            q = c
            cur.append(c)
        elif c == "#" and (not cur or cur[-1] in " \t"):      # a shell comment runs to the end of the line; its apostrophes must not open a quote
            while i < n and cmd[i] != "\n":
                i += 1
            continue
        elif c in ";|&\n":
            out.append("".join(cur))
            cur = []
            if c in "|&" and i + 1 < n and cmd[i + 1] == c:
                i += 1
        else:
            cur.append(c)
        i += 1
    out.append("".join(cur))
    if q is not None:                                           # an unterminated quote swallowed the rest: fall back to a plain split so later statements are still judged
        return [s for s in re.split(r"[;|&\n]+", cmd) if s.strip()]
    return out


def _tokens(stmt):
    try:
        return shlex.split(stmt, posix=True)
    except ValueError:
        return stmt.split()


NON_RUNNERS = frozenset(("git", "gh", "diff", "cp", "mv", "copy", "move", "del", "rm", "ls", "dir", "type", "cat", "grep", "rg", "sed", "awk", "head", "tail", "less", "more", "wc", "stat",
                         "file", "vim", "code", "notepad", "xargs-n0", "pyflakes", "flake8", "black", "py_compile", "findstr", "select-string", "get-content", "gc"))


def _runs_script(toks, rx, stmt):
    """True when the statement RUNS the script: the script is the command itself, or the first program argument of python (past its options). A statement that only mentions the
    script in a quoted argument (a prompt for another tool, a commit message, a grep) does not run it."""
    i = _command_word_index(toks)
    if i < 0:
        return False
    first = os.path.basename(toks[i].replace("\\", "/")).lower()
    if first.endswith(".exe"):
        first = first[:-4]
    if first in NON_RUNNERS:
        return False
    try:
        raw = [t.strip("\"'") for t in shlex.split(stmt, posix=False)]          # backslashes in a Windows path survive here (posix mode eats them)
    except ValueError:
        raw = [t.strip("\"'") for t in stmt.split()]
    ri = _command_word_index(raw)
    if ri < 0:
        return False

    def named(tok):
        return bool(rx.search(" " + re.split(r"[\\/]", tok)[-1]))
    if first.startswith("python") or first in ("py", "pythonw"):
        j = ri + 1
        while j < len(raw) and raw[j].startswith("-"):
            j += 2 if raw[j] in ("-X", "-W", "-m") else 1
        return j < len(raw) and named(raw[j])
    return named(raw[ri])


def _is_env(tok):
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok))


def _command_word_index(toks):
    """Index of the token that is the COMMAND: past env assignments and wrapper words (timeout 60, nice -n 10, sudo, env, nohup, time, command ...) with their options and numbers."""
    i, n = 0, len(toks)
    while i < n:
        t = toks[i]
        if _is_env(t) or t in ("{", "(", "&", "!", "then", "do", "else", "elif"):
            i += 1
            continue
        base = os.path.basename(t.replace("\\", "/")).lower()
        if base.endswith(".exe"):
            base = base[:-4]
        if base in WRAPPERS:
            i += 1
            while i < n and (toks[i].startswith("-") or re.match(r"^[\d.]+[smhd]?$", toks[i]) or _is_env(toks[i])):
                i += 1
            continue
        return i
    return -1


def _command_index(toks, names):
    """Index of the command word if its basename is one of `names`; -1 otherwise (an argument that happens to be called git or claude is not the command)."""
    i = _command_word_index(toks)
    if i < 0:
        return -1
    base = os.path.basename(toks[i].replace("\\", "/")).lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return i if base in names else -1


def _flag_values(stmt, flag):
    """Every value after --flag (or --flag=value) in the statement, including ones that sit inside the message text: the classifier takes the worst of them."""
    return [m.group(1).strip("\"'") for m in re.finditer(r"--%s(?:\s+|=)(\"[^\"]*\"|'[^']*'|\S+)" % re.escape(flag), stmt, re.I)]


def _flag_value(stmt, flag):
    """The first value after --flag (or --flag=value): a literal number, a variable or substitution, or None when the flag is absent."""
    vals = _flag_values(stmt, flag)
    return vals[0] if vals else None


def _git_external(toks):
    i = _command_index(toks, {"git"})
    if i < 0:
        return False
    j = i + 1
    while j < len(toks) and toks[j].startswith("-"):
        j += 2 if toks[j] in GIT_VALUE_OPTS else 1
    return j < len(toks) and toks[j] == "push" and not any(t in ("--help", "-h", "--dry-run", "-n") for t in toks[j + 1:])


def _gh_external(toks):
    i = _command_index(toks, {"gh"})
    if i < 0:
        return False
    rest = [t for t in toks[i + 1:] if not t.startswith("-")]
    if len(rest) >= 2 and (rest[0], rest[1]) in GH_SENDS:
        return True
    if rest[:1] == ["api"]:
        return (any(t.upper() in SEND_METHODS for t in toks) or any(re.match(r"^--method=(POST|PUT|PATCH|DELETE)$", t, re.I) for t in toks)
                or any(t in ("-f", "-F", "--field", "--raw-field", "--input") for t in toks))
    return False


def _is_local_url(u):
    """True only when the URL's host (not a word in its path) is this machine."""
    try:
        from urllib.parse import urlparse
        return (urlparse(u).hostname or "").lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    except ValueError:
        return False


def _curl_external(toks, stmt):
    i = _command_index(toks, {"curl"})
    if i < 0:
        return False
    rest = toks[i + 1:]
    sends = False
    for k, t in enumerate(rest):
        if t in ("-X", "--request") and k + 1 < len(rest) and rest[k + 1].upper() in SEND_METHODS:
            sends = True
        elif re.match(r"^-X(POST|PUT|PATCH|DELETE)$", t, re.I) or re.match(r"^--request=(POST|PUT|PATCH|DELETE)$", t, re.I):
            sends = True
        elif any(t == f or (t.startswith(f) and (f.startswith("--") or len(t) > len(f))) for f in CURL_DATA) and not t.startswith("--dns"):
            sends = True
        elif re.match(r"^-[A-Za-z]{2,}$", t) and re.search(r"[dFT]", t):      # a cluster such as -sd or -fsSF carries a data flag
            sends = True
    if not sends:
        return False
    urls = URL.findall(stmt) or ["http://" + t for t in rest if re.match(r"^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?(?:/\S*)?$", t)]
    return not (urls and all(_is_local_url(u) for u in urls))


def _network_sender(toks):
    """scp, sftp, ftp, nc and friends always talk to another machine; rsync only when an argument has a user@host: or host: remote part."""
    i = _command_index(toks, {"scp", "sftp", "ftp", "nc", "ncat", "netcat", "telnet", "rsync", "ssh", "plink", "pscp"})
    if i < 0:
        return False
    base = os.path.basename(toks[i].replace("\\", "/")).lower().replace(".exe", "")
    if base != "rsync":
        return not any(t in ("--help", "-h", "-V", "--version") for t in toks[i + 1:])
    return any(re.match(r"^[\w.-]+@[\w.-]+:|^[\w.-]+\.[\w.-]+:[^\\/]", t) for t in toks[i + 1:])


SSH_VALUE_FLAGS = frozenset(("-b", "-c", "-D", "-E", "-F", "-I", "-i", "-J", "-L", "-l", "-m", "-O", "-o", "-p", "-Q", "-R", "-S", "-W", "-w", "-B"))
SCP_VALUE_FLAGS = frozenset(("-c", "-F", "-i", "-J", "-l", "-o", "-P", "-S"))
SSH_SAFE_O_KEYS = frozenset(("stricthostkeychecking", "connecttimeout", "batchmode", "serveraliveinterval", "serveralivecountmax", "loglevel"))   # options that tune the connection but cannot change which host it reaches


def _has_unsafe_o_flag(rest):
    """True if any token carries J, F, or a not-on-the-allowlist o - standalone (-J, -F, -o Key=Val), combined with a value (-oKey=Val), or bundled with
    other short flags (-vJhost, -4o, ...). getopt-style clustering lets ssh/scp combine boolean flags with one value-taking flag at the end of the token,
    so any occurrence of J/F/o anywhere in a single-dash token fails closed, except the two exact recognized safe shapes (security review 2026-11-04,
    round 3: a prefix check alone missed bundled clusters like -vJhost)."""
    k = 0
    while k < len(rest):
        t = rest[k]
        if t == "-o":
            val = rest[k + 1] if k + 1 < len(rest) else ""
            key = val.split("=", 1)[0].strip().lower()
            if key not in SSH_SAFE_O_KEYS:
                return True
            k += 2
            continue
        if t.startswith("-") and not t.startswith("--") and len(t) > 1:
            body = t[1:]
            if body[0] == "o" and len(body) > 1:
                key = body[1:].split("=", 1)[0].strip().lower()
                if key not in SSH_SAFE_O_KEYS:
                    return True
            elif any(c in "JFo" for c in body):
                return True
        k += 1
    return False


def _remote_target_hosts(toks, i, base):
    """The actual remote host(s) a ssh/scp/rsync/sftp/plink/pscp statement would connect to, exactly (not by substring), or None when a flag could
    redirect the real connection (-J/-F/-o ProxyCommand=.../--rsh/-e) and the real destination can't be trusted from the visible hostname argument alone."""
    rest = toks[i + 1:]
    if base in ("ssh", "sftp", "plink", "pscp", "scp") and _has_unsafe_o_flag(rest):    # security review 2026-11-04: checking only the next token's keywords missed -oHostname=, -oInclude=, combined -oFoo forms, and silently let unrecognized -o options through; now allowlist-based, fails closed by default
        return None
    if base in ("ssh", "sftp", "plink", "pscp"):
        hosts, k = [], 0
        while k < len(rest):
            t = rest[k]
            if t in SSH_VALUE_FLAGS:
                k += 2
                continue
            if t.startswith("-") and len(t) > 1:
                k += 1
                continue
            hosts.append(t.split("@")[-1])
            break
        return hosts
    if base == "scp":
        hosts, k = [], 0
        while k < len(rest):
            t = rest[k]
            if t in SCP_VALUE_FLAGS:
                k += 2
                continue
            if t.startswith("-") and len(t) > 1:
                k += 1
                continue
            if re.match(r"^[A-Za-z]:[\\/]", t):     # a Windows drive-letter local path (C:\...), not a remote host:path spec - same ambiguity real scp.exe resolves the same way
                k += 1
                continue
            m = re.match(r"^(?:[\w.-]+@)?([\w.-]+):", t)
            if m:
                hosts.append(m.group(1))
            k += 1
        return hosts
    if base == "rsync":
        if any(t in ("-e", "--rsh") or t.startswith(("-e", "--rsh=")) for t in rest):
            return None       # a custom remote shell could route anywhere regardless of the hostname shown
        hosts = []
        for t in rest:
            m = re.match(r"^[\w.-]+@([\w.-]+):", t) or re.match(r"^([\w.-]+\.[\w.-]+):(?:$|[^\\/])", t)
            if m:
                hosts.append(m.group(1))
        return hosts
    return None   # nc/ncat/netcat/telnet/ftp: no exact-match carve-out, always needs his approval


def _wget_external(toks):
    i = _command_index(toks, {"wget"})
    return i >= 0 and any(t.startswith(("--post-data", "--post-file")) or t.lower() in ("--method=post", "--method=put") for t in toks[i + 1:])


def _rank(c):
    return 3 if (c["external"] and not c.get("standing")) else 2 if c["external"] else 1


def _discord_class(stmt, lookup):
    """Judge every --user / --channel / --reply-to value in the statement and return the worst, so a decoy flag inside the message text cannot hide the real one."""
    classes = [_discord_one(stmt, lookup, us, None, None) for us in _flag_values(stmt, "user")]
    classes += [_discord_one(stmt, lookup, None, ch, None) for ch in _flag_values(stmt, "channel")]
    classes += [_discord_one(stmt, lookup, None, None, rt) for rt in _flag_values(stmt, "reply-to")]
    if not classes:
        return _discord_one(stmt, lookup, None, None, None)
    return max(classes, key=_rank)


def _discord_one(stmt, lookup, us, ch, rt):
    if _owner_id() is None:     # not configured yet (no logs/discord_config.json owner_id): fail closed, never guess who "the owner" is
        return dict(external=True, kind="discord_not_configured", target="", standing=False, why="no owner_id set in logs/discord_config.json yet, so every Discord send is treated as external until that's configured")
    if us is not None:
        if us.isdigit():
            return dict(external=int(us) != _owner_id(), kind="discord_user", target=us, standing=False, why="send to a user id")
        return dict(external=True, kind="discord_user", target=us[:20], standing=False, why="send to a user id given as a variable or substitution")
    if ch is not None:
        if ch.isdigit() and int(ch) in STANDING_CHANNELS:         # the shared group channel: casual posts are a standing exception like replies there
            return dict(external=True, kind="discord_group_post", target=ch, standing=True, why="post in the group channel")
        return dict(external=True, kind="discord_channel", target=ch[:20], standing=False, why="send to a channel id")
    if rt is not None:
        if not rt.isdigit():
            return dict(external=True, kind="discord_reply_unknown", target=rt[:20], standing=False, why="reply-to given as a variable or substitution, cannot tell who it goes to (to reach the owner use --dm, or --reply-to with the literal id)")
        row = lookup(int(rt)) if lookup else None
        if row is None:
            return dict(external=True, kind="discord_reply_unknown", target=rt, standing=False, why="reply to a message not found in the incoming log (to reach the owner use --dm instead of --reply-to)")
        src, author = row.get("source"), row.get("author_id")
        if src == "guild":                                    # a reply in the group channel is seen by the friends there, whoever wrote the message it answers (review 2026-10-30)
            return dict(external=True, kind="discord_group_reply", target=str(row.get("channel_id")), standing=True, why="reply inside the group channel")
        if author == _owner_id() or (src == "dm" and author in (None, _owner_id())):
            return dict(external=False, kind="discord_reply_owner", target="owner", standing=True, why="reply to the owner")
        if src == "dm_friend":
            return dict(external=True, kind="discord_friend_reply", target=str(author), standing=True, why="reply to a close friend's DM")
        return dict(external=True, kind="discord_reply_other", target=str(author), standing=False, why="reply to a DM from someone else")
    return dict(external=False, kind="discord_default", target="owner", standing=True, why="no target flag: goes to the owner")


def _telegram_class(stmt):
    """A Telegram send. A send to his own owner chat_id or a reply into the one group he allowed (an approved friend's) is a standing exception like the Discord group; anything else
    needs his approval. telegram_bot_send.py's --reply-to is always <chat_id>[:<message_id>] (the chat_id is required even for a bare DM with no message being replied to),
    so the colon-and-message-id is optional here."""
    m = re.search(r"--reply-to(?:\s+|=)(-?\d+)(?::\d+)?", stmt)
    if m and int(m.group(1)) in TELEGRAM_STANDING_CHATS:
        return dict(external=True, kind="telegram_standing", target=m.group(1), standing=True, why="telegram send to his own chat or the group he allowed")
    return dict(external=True, kind="telegram", target="telegram", standing=False, why="a Telegram send script")


def _mask(text):
    """Pure. The same text with quoted strings and comments overwritten by filler of the same length, so positions still line up but nothing inside a string or comment counts."""
    text = text or ""
    out, q, i, n = [], None, 0, len(text)
    while i < n:
        c = text[i]
        if q:
            if c == "\\" and q == '"' and i + 1 < n:
                out.append("xx")
                i += 2
                continue
            out.append(c if c == q else "x")
            if c == q:
                q = None
        elif c in "'\"":
            q = c
            out.append(c)
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            while i < n and text[i] != "\n":
                out.append("x")
                i += 1
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _resolve_numeric_vars(text):
    """Pure. `R=156477...; python discord_queue_send.py --reply-to $R` is how replies are usually written: substitute a variable so the reply-to id can be looked up. Only an
    assignment that is its own statement (NAME=value; or on its own line, optionally exported), made exactly once in the whole command, never read/unset/declared/looped over,
    counts, and only `$NAME` AFTER it is replaced. A prefix assignment (NAME=1 cmd) is not visible to a later `$NAME` in a real shell, so it stays unresolved. Values are a long
    number or one plain word (a path, a script or a program name such as curl). Third review 2026-10-30: the first version was global and last-wins."""
    text = text or ""
    masked = _mask(text)
    assigns = {}
    for m in re.finditer(r"(?:^|[;\n]|&&)[ \t]*(?:export[ \t]+)?([A-Za-z_]\w*)=(\d{6,}|[\w./\\:@-]{1,120})[ \t]*(?=;|\n|&&|$)", masked):
        name = m.group(1)
        if len(re.findall(r"(?<![\w$])%s\+?=" % re.escape(name), masked)) != 1:
            continue
        if re.search(r"\b(?:read|unset|declare|typeset|local|for)\b[^;\n]*\b%s\b|printf\s+-v\s+%s\b" % (re.escape(name), re.escape(name)), masked):
            continue
        assigns[name] = (text[m.start(2):m.end(2)], m.end())
    if not assigns:
        return text

    def rep(m):
        a = assigns.get(m.group(1) or m.group(2))
        return a[0] if a and m.start() >= a[1] else m.group(0)
    return re.sub(r"\$(?:\{(\w+)\}|(\w+))", rep, text)


def _is_gate_cli(toks, stmt):
    """Pure. True only when the statement IS the gate's own approve / approve-last / report command (python [flags] send_gate_hook.py approve ...), with nothing that runs other code
    (substitution, process substitution, eval). Anything else that merely mentions the phrase is judged normally."""
    if re.search(r"\$\(|\$\{|`|<\(|>\(|\beval\b", stmt):
        return False
    i = _command_word_index(toks)
    if i < 0:
        return False
    base = os.path.basename(toks[i].replace("\\", "/")).lower().replace(".exe", "")
    if base.startswith("python") or base in ("py", "pythonw"):
        i += 1
        while i < len(toks) and toks[i].startswith("-"):
            i += 1
    return i + 1 < len(toks) and os.path.basename(toks[i].replace("\\", "/")).lower() == "send_gate_hook.py" and toks[i + 1] in ("approve", "approve-last", "report")


def _spawns_claude(body):
    """Pure. True when a statement's COMMAND starts a headless claude run (`claude -p ...`, `claude.exe --print`, also after `timeout 60`, inside `bash -c`, or via a python subprocess
    list); a commit message or an echo that only contains the words does not count."""
    if RX_PY_SPAWN_CLAUDE.search(body or ""):
        return True
    for stmt in _split(body):
        toks = _tokens(stmt)
        i = _command_word_index(toks)
        if i < 0:
            continue
        base = os.path.basename(toks[i].replace("\\", "/")).lower().replace(".exe", "")
        if base == "claude" and any(t in ("-p", "--print") for t in toks[i + 1:]):
            return True
        if base in ("bash", "sh", "zsh", "dash", "cmd", "powershell", "pwsh"):
            for k in range(i + 1, len(toks) - 1):
                if re.match(r"^(-[A-Za-z]*c|-command|/c|/k)$", toks[k], re.I) and _spawns_claude(toks[k + 1]):
                    return True
    return False


def _pipes_into_interpreter(body):
    """Pure. True when a top-level pipe feeds a shell or an interpreter that reads its program from stdin: `| bash`, `| "sh"`, `| /bin/bash`, `| sudo bash`, `| env bash`, `|& sh`,
    `| $SHELL`, `| python3 -` (or python with no argument), `| node`, `| iex`. `| python -c "..."` is code in an argument, not stdin, and is not flagged. Quotes, comments and
    `||` are understood."""
    text = body or ""
    q, i, n = None, 0, len(text)
    while i < n:
        c = text[i]
        if q:
            if c == "\\" and q == '"':
                i += 2
                continue
            if c == q:
                q = None
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif c == "|":
            if i + 1 < n and text[i + 1] == "|":
                i += 2
                continue
            j = i + 1
            if j < n and text[j] == "&":
                j += 1
            seg = re.split(r"[;|&\n]", text[j:], maxsplit=1)[0]
            toks = _tokens(seg)
            k = _command_word_index(toks)
            if k >= 0:
                base = os.path.basename(toks[k].replace("\\", "/")).lower().replace(".exe", "")
                args = toks[k + 1:]
                if base.startswith("$") or base in SHELL_NAMES:
                    return True
                if (base in STDIN_INTERPRETERS or base.startswith("python")) and (not args or args[0] == "-"):
                    return True
        i += 1
    return False


def _unquoted(text):
    """Pure. The text with quoted strings blanked, so a `|cmd` inside a regex or a message is not read as a pipe into a shell."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", text or "")


def _none():
    return dict(external=False, kind="none", target="", standing=False, why="")


def _classify_stmt(stmt, lookup, depth=0):
    toks = _tokens(stmt)
    while toks and _is_env(toks[0]):
        toks = toks[1:]
    if not toks:
        return _none()
    if _is_gate_cli(toks, stmt):                                    # the gate's own approve / report command: it refuses by itself without his yes, and its --text may quote a send command
        return _none()
    if RX_OUTBOX.search(stmt) and (RX_OUTBOX_REDIRECT.search(stmt) or _is_writer(stmt)):          # writing straight into the Discord / Telegram outbox: the daemon sends whatever it finds there
        return dict(external=True, kind="outbox_write", target="outbox", standing=False, why="writes a message file straight into the send queue the daemon delivers from")
    if depth == 0 and re.match(r"^\$\{?\w+\}?$", toks[0]) and not (len(toks) > 1 and re.match(r"^(?:=|\+=|-=|\*=|\|)", toks[1])):          # a bare variable as the command (PowerShell $x = ... is an assignment, and code inside powershell -c is not judged by this rule)
        return dict(external=True, kind="unresolved_command", target=toks[0][:20], standing=False, why="the command is a shell variable that cannot be resolved, so what runs cannot be seen")
    if toks[0].lower().strip("\"'") in TALKERS:
        if depth < 3:                                               # `echo done $(git push)`: the printing word is harmless, what is substituted into it is not
            for inner in re.findall(r"\$\(([^()]*)\)", stmt) + re.findall(r"`([^`]*)`", stmt) + re.findall(r"[<>]\(([^()]*)\)", stmt):
                for s2 in _split(_strip_heredocs(inner)):
                    c = _classify_stmt(s2, lookup, depth + 1)
                    if c["external"]:
                        return c
        return _none()
    if _runs_script(toks, RX_DISCORD, stmt) or RX_DISCORD_MOD.search(stmt):
        return _discord_class(stmt, lookup)
    if _runs_script(toks, RX_TELEGRAM, stmt):
        return _telegram_class(stmt)
    if RX_MAIL.search(stmt):
        return dict(external=True, kind="mail", target="mail", standing=False, why="mail send")
    if _git_external(toks) or _gh_external(toks):
        return dict(external=True, kind="repo", target="github", standing=False, why="a pull request, issue, push or release")
    if _network_sender(toks):
        ni = _command_index(toks, {"scp", "sftp", "ftp", "nc", "ncat", "netcat", "telnet", "rsync", "ssh", "plink", "pscp"})
        nbase = os.path.basename(toks[ni].replace("\\", "/")).lower().replace(".exe", "")
        nhosts = _remote_target_hosts(toks, ni, nbase)
        if nhosts and all(h.lower().rstrip(".") in RIG_COMPUTE_HOSTS for h in nhosts):
            return dict(external=True, kind="network_send", target="own-compute-host", standing=True, why="the owner's own compute host, administered directly per their own standing go-ahead")
        if nhosts and all(h.lower().rstrip(".") in NITE_HOSTS for h in nhosts):
            return dict(external=True, kind="network_send", target="nite", standing=True, why="the owner's own second host, same standing go-ahead")
        return dict(external=True, kind="network_send", target="remote host", standing=False, why="a file or data copy to another machine (scp, rsync, sftp, ftp, nc)")
    if _curl_external(toks, stmt) or _wget_external(toks):
        return dict(external=True, kind="web_post", target="web", standing=False, why="a web request that sends data to a non-local host")
    if RX_IWR_POST.search(stmt):
        urls = URL.findall(stmt)
        if not (urls and all(_is_local_url(u) for u in urls)):
            return dict(external=True, kind="web_post", target="web", standing=False, why="a PowerShell web request that sends data to a non-local host")
    if depth < 3:                                                   # bash -c "..." / timeout 60 bash -lc "..." / powershell -Command "..." / eval "..." / $(...): judge the inner command too
        inners = []
        si = _command_index(toks, {"bash", "sh", "zsh", "dash", "powershell", "pwsh", "cmd"})
        if si >= 0:
            for k in range(si + 1, len(toks) - 1):
                if re.match(r"^(-[A-Za-z]*c|-command|/c|/k)$", toks[k], re.I):
                    inners.append(" ".join(toks[k + 1:]) if toks[k].lower() in ("/c", "/k") else toks[k + 1])           # cmd /c takes the whole rest of the line as the command
        ei = _command_index(toks, {"eval"})
        if ei >= 0 and ei + 1 < len(toks):
            inners.append(" ".join(toks[ei + 1:]))
        inners += re.findall(r"\$\(([^()]*)\)", stmt) + re.findall(r"`([^`]*)`", stmt) + re.findall(r"[<>]\(([^()]*)\)", stmt)
        for inner in inners:
            for s2 in _split(_strip_heredocs(inner)):
                c = _classify_stmt(s2, lookup, depth + 1)
                if c["external"]:
                    return c
    return _none()


def _mcp_class(name, unattended=False):
    low = name.lower()
    action = low.split("__", 2)[-1]
    toks = [t for t in re.split(r"[_\-]+", action) if t]
    if any(s in low for s in MCP_SKIP) or "claude-in-chrome" in low:
        if unattended and any(t in UNATTENDED_MUTATE for t in toks):
            return dict(external=True, kind="mcp_act", target=name, standing=False, why="a browser or desktop tool that acts (click, type, run), in a run nobody is watching")
        return _none()
    if toks and toks[0] in MCP_READ_FIRST:
        return _none()
    if any(t in MCP_SEND_TOKENS for t in toks) or any(c in action for c in MCP_SEND_COMPOUND):
        return dict(external=True, kind="mcp_send", target=name, standing=False, why="an MCP tool that looks like a send")
    return _none()


def _python_blocks(full):
    """Pure. The python source inside a command: heredoc bodies fed to python and the string argument of `-c` (also inside `bash -c "python -c ..."`). Empty if none can be found."""
    blocks = [m.group(2) for m in RX_PY_HEREDOC.finditer(full or "")]
    blocks += [m.group(1) for m in re.finditer(r"-\w*c\s+(\"(?:\\.|[^\"\\])*\"|'[^']*')", full or "")]
    return blocks


def _local_only_code(block):
    """Pure. True when every URL in a python snippet is a literal address on this machine AND every .post/.put/.patch/.delete/Request call names its URL as such a literal: a local
    URL used as a decoy next to a destination built at run time (\'https://\' + host) does not make the snippet local (third review 2026-10-30)."""
    urls = URL.findall(block)
    if not urls or not all(_is_local_url(u) for u in urls):
        return False
    if re.search(r"['\"]https?://['\"]|https?://['\"]\s*\+|['\"]https?:?//?['\"]\s*\+", block):
        return False
    return not re.search(r"(?:\.(?:post|put|patch|delete)|\bRequest)\s*\(\s*(?!f?['\"]https?://[^'\"\s]+['\"])", block)


def _code_sends(full):
    blocks = _python_blocks(full) or [full]
    for blk in blocks:
        if CODE_SEND.search(blk) and not _local_only_code(blk):
            return True
    return False


# ---- tier 1b, Fable 5.2 review 2026-11-16: "when a command runs a script, resolve the file and scan it for network use" ----
# The real gap this closes: the existing CODE_SEND check only ever looked at code written INLINE in the command (a -c string or a heredoc). A script
# written earlier with Write/Edit, then run as `python file.py`, was never content-scanned at all -- found independently while checking Fable's
# narrower claim that `python -c "requests.post(...)"` bypasses the gate (that specific claim was already false; this broader gap was real).
_SCRIPT_RUNNERS = (
    (re.compile(r"(?<![\w.])(?:python[0-9.]*(?:\.exe)?|pythonw|py)\b(?:\s+-\S+)*\s+[\"']?(?P<path>[^\s|;&<>\"']+\.py)[\"']?", re.I), "python"),
    (re.compile(r"(?<![\w.])node(?:\.exe)?\b(?:\s+-\S+)*\s+[\"']?(?P<path>[^\s|;&<>\"']+\.js)[\"']?", re.I), "node"),
    (re.compile(r"(?<![\w.])(?:ba|z|da)?sh(?:\.exe)?\b(?:\s+-\S+)*\s+[\"']?(?P<path>[^\s|;&<>\"']+\.sh)[\"']?", re.I), "shell"),
    (re.compile(r"(?<![\w.])(?:powershell|pwsh)(?:\.exe)?\b[^\n]*?-File\s+[\"']?(?P<path>[^\s|;&<>\"']+\.ps1)[\"']?", re.I), "powershell"),
)
MAX_SCRIPT_SCAN_BYTES = 2_000_000
SCRIPT_NETWORK_HINTS = re.compile(
    r"\brequests\.|\burllib|\bhttpx\.|\bsocket\.(?:socket|create_connection)\b|\bsmtplib\b|\bwebhook\b"
    r"|require\(['\"]https?['\"]\)|require\(['\"]net['\"]\)|\bfetch\s*\(|\baxios\b"
    r"|\bcurl\b|\bwget\b|Invoke-WebRequest|Invoke-RestMethod|New-Object\s+(?:System\.)?Net\.\w*(?:Client|Request)", re.I)


def _resolve_script_args(full):
    """Pure. -> [(lang, raw_path), ...] for each local script file a command appears to run directly (python/node/shell/powershell with a bare file
    argument, not -c/-m/stdin, which are already handled elsewhere). Best-effort textual extraction, same spirit as the rest of this file's parsing."""
    out = []
    for rx, lang in _SCRIPT_RUNNERS:
        for m in rx.finditer(full or ""):
            out.append((lang, m.group("path")))
    return out


def _scan_script_file(path, cwd=None, safe_roots=None):
    """Impure (reads the filesystem). -> (status, detail) where status is "flagged" (network code found), "clean", or "unreadable" (does not exist,
    too large, outside a known safe root, or any OSError). Fable 5.2's spec says "fail closed" on unreadable; classify() below instead makes the
    "flagged" (confirmed network code) case a real `external=True` block, and the "unreadable" case a `external=False` shadow-logged signal, not a
    block -- cwd for a Bash call is only approximated here (PreToolUse does not hand this hook the tool call's real working directory), so a hard
    block on every unresolved relative path risked flooding false positives on ordinary, already-authorized local work. Visibility first, enforce
    once the real false-positive rate on a week of real traffic is known -- the same rollout pattern every other gate in this file used."""
    roots = safe_roots if safe_roots is not None else SCRIPT_SAFE_ROOTS
    try:
        full_path = path if os.path.isabs(path) else os.path.join(cwd or os.getcwd(), path)
        full_path = os.path.normpath(full_path)
        if not any(full_path.lower().startswith(os.path.normpath(r).lower()) for r in roots):
            return "unreadable", "resolves outside the known safe roots: %s" % full_path
        if not os.path.isfile(full_path):
            return "unreadable", "no such file: %s" % full_path
        if os.path.getsize(full_path) > MAX_SCRIPT_SCAN_BYTES:
            return "unreadable", "too large to scan (%d bytes): %s" % (os.path.getsize(full_path), full_path)
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError as e:
        return "unreadable", str(e)
    if _is_known_file(full_path, KNOWN_SAFE_TEST_FIXTURES):   # exact path + content hash, not a name pattern (2nd security review 2026-11-16)
        return "clean", full_path
    if _canon_path(full_path) == _canon_path(SELF_PATH):   # path-only, deliberately no hash -- see SELF_PATH's own comment for why
        return "clean", full_path
    is_py = full_path.lower().endswith(".py")
    hit = bool((is_py and CODE_SEND.search(content)) or SCRIPT_NETWORK_HINTS.search(content))
    return ("flagged" if hit else "clean"), full_path


SCRIPT_SAFE_ROOTS = (r"J:\Jarvis2", tempfile.gettempdir())
UNSCANNABLE_SCRIPT_LOG = "unscannable_script.jsonl"
# Bound to an exact canonical path AND the file's own content hash (2nd security review, 2026-11-16: a basename-only allowlist is still a fail-open
# allowlist -- anyone with write access, including me, could defeat it by creating a same-named file anywhere a safe root reaches). Both the path's
# real, subst-resolved identity and its content must match a known-good entry, so neither planting a same-named file elsewhere nor silently editing
# the real one keeps the exemption -- an edited file just falls through to being scanned normally, exactly like everything else.
KNOWN_MESSAGING_SCRIPTS = {
    r"J:\Jarvis2\scripts\discord_queue_send.py": "ed57ddc0b0a18c0ae1cb2670fc19092d1f3e5be3d53b5e17ab89fa043f5da091",
    r"J:\Jarvis2\scripts\telegram_queue_send.py": "e2b1b3c82c080171bcf3c99534c20965375e7686d2655b8ff7ae5965084c7ddc",
    r"J:\Jarvis2\scripts\telegram_bot_send.py": "3bf660d626e9d72e2a6795e70848e00bc3167651228036aabc0c0503bb71c586",
    r"J:\Jarvis2\scripts\telegram_send.py": "7b365273e29f9ceed74ad8a5545264c8e28ce74ac5b7cd0d50a5d4c6daa8ac40",
    r"J:\Jarvis2\scripts\comms_say.py": "98445abf14b426feac5d42e5bb4c3c1880ee8054805cc78cb2196c053aee0693",
}
KNOWN_SAFE_TEST_FIXTURES = {
    # the one real false positive found live: a lint-test's own sample string mentioning "webhook"
    r"J:\Jarvis2\scripts\test_discord_send_lint.py": "d329259cd5388034c5bf87d5b86cfdfe611df86829971f6d8900262aa647522b",
}
# send_gate_hook.py is deliberately exempted by PATH ONLY, never hash-bound, unlike every other exemption above -- a hash-bound entry for this
# file would be self-referential (editing the file to add/update the entry changes the file's own hash, invalidating the entry that edit just
# added) and would need recomputing on every future change to this exact file. This is safe without a hash because a malicious edit to this
# real file is already caught by a separate, independent control: _is_tamper()'s gate_tamper check, which specifically watches writes to and
# reads of this file's own protected name regardless of this exemption. The false positive this fixes is real and was found live: this file's
# own CODE_SEND/SCRIPT_NETWORK_HINTS detection regexes literally contain the strings "requests.post", "curl", "smtplib" etc as PATTERNS to
# detect, not as real calls, so running `python send_gate_hook.py approve-last` (its own legitimate CLI) was flagged as "a script that sends
# data out" by tier 1b's blunt content scan.
SELF_PATH = os.path.abspath(__file__)


def _canon_path(path):
    return os.path.normcase(os.path.realpath(path))


def _is_known_file(resolved_path, known_map):
    """Impure (reads the file to hash it). True only when resolved_path's canonical, subst-resolved identity matches a known entry's key AND the
    file's current sha256 matches that entry's recorded hash. known_map: {literal_path: sha256_hex}."""
    canon = _canon_path(resolved_path)
    for literal, expected_hash in known_map.items():
        if _canon_path(literal) == canon:
            try:
                with open(resolved_path, "rb") as f:
                    return hashlib.sha256(f.read()).hexdigest() == expected_hash
            except OSError:
                return False
    return False


def _log_unscannable_script(command_text, why, logs):
    """Shadow-only record of a script run the gate could not verify as network-free -- see _scan_script_file's docstring for why this is log-only
    rather than blocking in this first rollout."""
    try:
        with open(os.path.join(logs, UNSCANNABLE_SCRIPT_LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "command_head": " ".join(str(command_text).split())[:200], "why": why}) + "\n")
    except OSError:
        pass


def classify(tool_name, tool_input, lookup=None, unattended=False):
    """Pure. -> {external, kind, target, standing, why}. `lookup(message_id)` returns the incoming-log row for a reply-to id (or None).
    standing=True marks an external call that his written rules already allow (group reply, close-friend reply)."""
    name = tool_name or ""
    tin = tool_input if isinstance(tool_input, dict) else {}
    if name in ("Bash", "PowerShell", "bash"):
        raw = str(tin.get("command", ""))
        if len(raw) > MAX_COMMAND:
            return dict(external=True, kind="oversized", target="command", standing=False, why="the command is too long to read (over %d characters): put it in a script file" % MAX_COMMAND)
        full = _resolve_numeric_vars(re.sub(r"\\\r?\n[ \t]*", " ", raw))           # a backslash-newline continuation is one line to the shell
        body = _strip_heredocs(full)
        if unattended and _spawns_claude(body):                       # a child claude -p would start without the unattended flag and in the live session's shadow mode
            return dict(external=True, kind="spawn_claude", target="claude", standing=False, why="starts another claude run from inside a run nobody is watching")
        if _is_tamper(full, body, unattended):
            return dict(external=True, kind="gate_tamper", target="gate", standing=False, why="changes the gate's own approval, switch or evidence files (or, in a run nobody watches, its code and settings), or calls its approval code from python instead of through his Discord yes")
        for sbody in _shell_heredoc_bodies(full):                       # `bash <<EOF` runs its body
            for s2 in _split(_strip_heredocs(sbody)):
                c = _classify_stmt(s2, lookup)
                if c["external"]:
                    return c
        if RX_PY_CODE.search(full) and _code_sends(full):               # python code (-c or a heredoc) that posts, mails or curls out, judged on the python itself
            return dict(external=True, kind="code_send", target="python", standing=False, why="python code that sends data out")
        shadow = None                                                   # tier 1b (Fable 5.2, 2026-11-16): a script WRITTEN earlier then run as a file, not inline -c/heredoc code.
        for lang, raw_path in _resolve_script_args(full):               # Unreadable-script shadow signals never short-circuit the rest of this function -- a compound command
            # Known messaging scripts (discord_queue_send.py etc) are skipped PER RESOLVED SCRIPT, not for the whole command (security review,
            # 2026-11-16, caught live: an earlier version skipped scanning the entire command whenever it merely mentioned discord_queue_send.py
            # anywhere, so "python discord_queue_send.py ok && python malicious.py" would have let malicious.py through unscanned too). They
            # genuinely contain network code by design, and the statement loop below already classifies them precisely by their actual arguments
            # (standing exceptions for a reply to the owner, the group channel, a close friend) -- this check is for a script the gate has NO
            # dedicated classifier for, not a substitute for one that already exists. Exact path + content hash (2nd security review 2026-11-16),
            # not a basename check -- a same-named file planted anywhere a safe root reaches must not inherit this exemption.
            cwd = os.getcwd()
            resolved = raw_path if os.path.isabs(raw_path) else os.path.join(cwd, raw_path)
            if os.path.isfile(resolved) and _is_known_file(resolved, KNOWN_MESSAGING_SCRIPTS):
                continue
            status, detail = _scan_script_file(raw_path, cwd=cwd)    # like "python helper.py && git push" must still have its git push half judged normally.
            if status == "flagged":
                return dict(external=True, kind="code_send", target="script_file", standing=False,
                            why="a %s script it runs (%s) sends data out" % (lang, detail))
            if status == "unreadable" and shadow is None:
                shadow = dict(external=False, kind="unscannable_script_shadow", target="script_file", standing=False,
                              why="runs a %s script (%s) that could not be verified safe: %s" % (lang, raw_path, detail))
        if any(RX_DECODE_EXEC.search(t) or RX_FETCH_RUN.search(t) or _pipes_into_interpreter(t) for t in [body] + _shell_heredoc_bodies(full)):          # eval "$(... | base64 -d)", bash <(curl ...): what runs is hidden or fetched
            return dict(external=True, kind="indirect_exec", target="shell", standing=False, why="decoded or downloaded text is run: what it does cannot be seen")
        if _pipes_into_interpreter(body):
            return dict(external=True, kind="indirect_exec", target="shell", standing=False, why="text piped into a shell or interpreter: what it runs cannot be seen")
        best = None
        for stmt in _split(body):
            c = _classify_stmt(stmt, lookup)
            if c["external"]:
                return c
            if best is None and c["kind"] != "none":
                best = c
        return best or shadow or _none()
    if name in ("Write", "Edit", "NotebookEdit"):                     # the file tools can reach the gate's own files too
        path = str(tin.get("file_path", "")).replace("\\", "/")
        base = os.path.basename(path).lower()
        if base in PROTECTED_NAMES or (unattended and base in CODE_PROTECTED_NAMES):
            return dict(external=True, kind="gate_tamper", target=base, standing=False, why="edits one of the gate's own approval, switch, evidence or (in a run nobody watches) code files")
        if RX_OUTBOX.search(path):
            return dict(external=True, kind="outbox_write", target="outbox", standing=False, why="writes a message file straight into the send queue the daemon delivers from")
        return _none()
    if name.lower().startswith("mcp__"):
        return _mcp_class(name, unattended)
    return _none()


APPROVAL_TTL = 30 * 60


def valid_approvals(rows, used_rows, now):
    """Pure. The set of call hashes with a live approval: written less than 30 minutes ago and not yet used. One approval covers one send."""
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


def decide(cls, hashes, mode, call_hash, unattended=False):
    """Pure. -> (decision, reason). decision in allow | standing | approved | would_block | block. shadow never blocks. The standing exceptions (group reply, close-friend
    reply) are for a live session with him around; a run nobody watches gets none of them and is always in enforce mode (independent review 2026-10-30)."""
    if not cls.get("external"):
        return "allow", ""
    if unattended:
        mode = "enforce"
    elif cls.get("standing"):
        return "standing", cls["why"]
    if call_hash in (hashes or ()):
        return "approved", "approval row found"
    if mode == "enforce":
        return "block", cls["why"]
    return "would_block", cls["why"]


SHORT_CODE_LEN = 6                 # Fable 5.2 review, 2026-11-16: "first 6 hex of the content hash" -- 4 was my first pass, 6 gives a wider margin
APPROVAL_CODE_WINDOW = 15 * 60      # a code found in a DM more than 15 min after the hold does not count, even though it is the right code


def short_code(content_hash_hex):
    """Pure. The hex code shown to him for one specific held call -- real finding (Fable 5.2 review, 2026-11-16): a generic "yes" after a hold was
    bound only by arrival time, never by whether the yes was actually about that content, so an unrelated yes-shaped DM sent after some other hold could
    be spent on it. Requiring this code in his reply ties the approval to the exact content, not just to timing."""
    return (content_hash_hex or "")[:SHORT_CODE_LEN]


def approval_evidence(dms, since, now, used=None, code=None, deadline=None):
    """Pure. The newest DM from the owner themself that arrived after `since` (epoch) and approves, or None. A message with a negation or a question mark is
    never a yes, and a message id already used for an approval (`used`) cannot approve a second thing. dms: rows of discord_incoming.jsonl.
    When `code` is given (the normal path now, see short_code()), the message must contain that exact code as a whole word -- a plain "yes" no longer
    counts, since the code is what actually binds the approval to this specific held call rather than to mere timing. `deadline` bounds how late a
    correct code may still arrive (Fable 5.2 review, 2026-11-16: a code typed correctly but long after the fact shouldn't count either); callers that
    want the earliest-hold-fixes-superseded-approvals behavior pass deadline = latest_hold_ts + APPROVAL_CODE_WINDOW explicitly rather than relying on
    `since` for both the lower and upper bound, since `since` may now be an earlier hold than the most recent attempt. Omitted, there is no extra cap
    beyond `now`. When `code` is None, falls back to the old generic yes-shaped-phrase match (kept for any caller with no specific call to bind to)."""
    if _owner_id() is None:     # not configured yet: never treat any message as the owner's approval
        return None
    best = None
    code_rx = re.compile(r"\b%s\b" % re.escape(code), re.I) if code else None
    cap = min(now, deadline) if deadline is not None else now
    for m in dms or []:
        if m.get("author_id") != _owner_id() or m.get("source") != "dm":
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
        return "enforce"            # a headless run nobody is watching (autopilot sets this) gets the real gate even while the live session is still in shadow
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


def _lookup_factory(logs):
    def lookup(mid):
        needle = '"id": %d' % mid
        try:
            for line in open(os.path.join(logs, "discord_incoming.jsonl"), encoding="utf-8", errors="replace"):
                if needle in line:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("id") == mid:
                        return row
        except OSError:
            pass
        return None
    return lookup


def _log(row, logs):
    try:
        with open(os.path.join(logs, LOG), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=True) + "\n")          # ASCII-escaped: a lone surrogate or odd character in a command must not make the log call raise (third review)
    except Exception:                                                     # noqa: BLE001 - logging must never decide a call
        pass


def approve(text, by, logs=None, by_msg=None):
    """Low-level writer (the CLI checks the evidence first). Appends one approval row for the exact call text."""
    logs = logs or LOGS
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hash": content_hash(text), "target": "exact call", "text_head": " ".join(text.split())[:80], "by": by, "by_msg": by_msg}
    with open(os.path.join(logs, APPROVALS), "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def run(payload, logs=None, now=None):
    """-> (exit_code, stderr_message). Pure-ish: reads the logs folder, appends to the gate log."""
    logs = logs or LOGS
    unattended = os.environ.get("JARVIS_UNATTENDED") == "1"
    if os.path.exists(os.path.join(logs, OFF_FILE)) and not unattended:          # a leftover kill-switch file must not silence the gate in a run nobody watches
        return 0, ""
    name = payload.get("tool_name", "")
    tin = payload.get("tool_input", {})
    text = tin.get("command") if isinstance(tin, dict) and "command" in tin else json.dumps(tin, sort_keys=True)
    try:                                                                # feed the budget breaker's identical-call counter; never let it break a call
        import budget
        budget.note_call("%s:%s" % (name, " ".join(str(text).split())[:150]), logs=logs)
    except (Exception, SystemExit):                                     # SystemExit too: a replaced budget.py that exits must not silence the gate
        pass
    cls = classify(name, tin, _lookup_factory(logs), unattended)
    if cls["kind"] == "unscannable_script_shadow":
        _log_unscannable_script(text, cls["why"], logs)
    if not cls["external"]:
        return 0, ""
    h = content_hash(text)
    mode = _mode(logs)
    now_t = time.time() if now is None else now
    decision, why = decide(cls, valid_approvals(_read(APPROVALS, logs), _read("approvals_used.jsonl", logs), now_t), mode, h, unattended)
    if decision == "approved":                                           # consume it: the same approval cannot cover a second send
        try:
            with open(os.path.join(logs, "approvals_used.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"hash": h, "t": now_t}) + "\n")
        except OSError:
            pass
    _log({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": name, "kind": cls["kind"], "target": cls["target"], "decision": decision, "mode": mode,
          "hash": h, "head": " ".join(str(text).split())[:120]}, logs)
    if decision == "block":
        return 2, ("send_gate: this would send something to someone other than the owner (%s: %s) and no approval exists for this exact content. "
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
    """-> exit code. Writes an approval only when the owner's own yes arrived after the hold."""
    logs = logs or LOGS
    now = time.time() if now is None else now
    if argv[0] == "approve":
        text = argv[argv.index("--text") + 1] if "--text" in argv else ""
        if not text:
            print("approve needs --text")
            return 2
        # bound to a held call (security review 2026-10-30): the text must be something the gate actually held, and only a code-bearing reply that
        # arrived AFTER that hold counts, so an unrelated earlier message cannot be spent on content he never saw. Bound to the FIRST hold of this exact
        # content, not the latest (Fable 5.2 review, 2026-11-16: the old "must be after the LATEST hold" rule caused a real superseded-approval bug --
        # a retry re-holding the same content pushed the window forward past a yes he'd already given; the content-bound code makes that ordering rule
        # unnecessary for security, so dropping it just removes an annoyance).
        mine = [r for r in _read(LOG, logs) if r.get("decision") in ("block", "would_block") and r.get("hash") == content_hash(text)]
        if not mine:
            print("the gate never held that exact content, so there is nothing to approve; run the call so it is held, show him the text, then ask")
            return 4
        try:
            since = time.mktime(time.strptime(mine[0]["ts"], "%Y-%m-%dT%H:%M:%S"))
            latest_ts = time.mktime(time.strptime(mine[-1]["ts"], "%Y-%m-%dT%H:%M:%S"))
        except (KeyError, ValueError):
            return 4
        head = text
        held_hash = mine[0]["hash"]
    else:
        held = [r for r in _read(LOG, logs) if r.get("decision") in ("block", "would_block")]
        if not held:
            print("nothing held")
            return 1
        since = latest_ts = time.mktime(time.strptime(held[-1]["ts"], "%Y-%m-%dT%H:%M:%S"))
        head = None
        held_hash = held[-1]["hash"]
    ev = approval_evidence(_read("discord_incoming.jsonl", logs), since, now, used={r.get("by_msg") for r in _read(APPROVALS, logs) if r.get("by_msg")},
                            code=short_code(held_hash), deadline=latest_ts + APPROVAL_CODE_WINDOW)
    if ev is None:
        print("no go-ahead from the owner found with the code %s within %d minutes of the hold; nothing recorded" % (short_code(held_hash), APPROVAL_CODE_WINDOW // 60))
        return 3
    if head is not None:
        row = approve(head, "owner", logs, by_msg=ev.get("id"))
    else:
        r = held[-1]
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "hash": r["hash"], "target": r.get("target"), "text_head": r.get("head", "")[:80], "by": "owner", "by_msg": ev.get("id")}
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
        if os.environ.get("JARVIS_UNATTENDED") == "1":                # any casual "ok" from him would otherwise approve a held send for a run nobody watches (second review 2026-10-30)
            print("approvals are not available inside an unattended run; ask him in a DM and let the live session approve it")
            return 2
        return _cli_approve(argv)
    try:                                                              # bytes, decoded as UTF-8: the console code page made emoji in a command raise and fail open
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        payload = json.loads(raw or "{}")
    except (ValueError, OSError):
        return 0
    try:
        code, msg = run(payload)
    except Exception as e:                       # fail open unless the call itself looks like a send, or the gate is enforcing
        text = str(payload)
        enforcing = os.environ.get("JARVIS_UNATTENDED") == "1" or _mode(LOGS) == "enforce"
        tname = str(payload.get("tool_name", "") if isinstance(payload, dict) else "").lower()
        if enforcing and (tname in ("bash", "powershell") or tname.startswith("mcp__")):
            sys.stderr.write("send_gate: internal error while judging this call (%s); refusing because the gate is enforcing. Retry once, or write the command into a script file.\n" % type(e).__name__)
            return 2
        looks = bool(RX_DISCORD.search(text) or RX_TELEGRAM.search(text) or RX_TAMPER.search(text)
                     or re.search(r"git\s+(?:-\S+\s+\S+\s+)*push|gh\s+(?:pr|issue|api|release)|curl|wget|Invoke-(?:Web|Rest)|requests\.(?:post|put)|smtplib|mcp__\w*(?:send|post|publish|push)", text, re.I))
        if looks and _mode(LOGS) == "enforce":
            sys.stderr.write("send_gate: internal error on a call that looks like a send (%s); refusing. Retry once, or ask the owner.\n" % type(e).__name__)
            return 2
        return 0
    if msg:
        sys.stderr.write(msg + "\n")
    return code


if __name__ == "__main__":
    sys.exit(main())
