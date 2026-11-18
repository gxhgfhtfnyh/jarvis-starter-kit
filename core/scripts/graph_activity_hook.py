# -*- coding: utf-8 -*-
"""
Feeds jarvis-ui's live 3D graph: watches Read/Write/Edit/Bash/PowerShell
calls and, on a real match, appends a "bloom this node" line to
J:\\Jarvis2\\logs\\jarvis_live_activity.jsonl. graph_bridge.py polls that
file and pushes the bloom into the running graph.

Deliberately conservative matching -- mirrors vault_access_log.py's style
(same silent-on-no-match, never-raise-into-the-hook-chain pattern):
  - Read/Write/Edit on a path under Jarvis-Brain\\*.md  -> Memory node
  - Read/Write/Edit on a path under Jarvis2\\scripts\\*   -> Skills node
  - a known script's basename appearing as a token in a Bash/PowerShell
    command string                                       -> same Skills node
  - if that script is listed under an app in graph_applications.json,
    ALSO bloom that Applications node
No match at all -> no bloom, nothing written. Silence reads as calm; this
deliberately does not light up a whole ring as a generic fallback.

Wired into PreToolUse in ~/.claude/settings.json (matcher:
"Read|Write|Edit|Bash|PowerShell") -- fires as the tool call starts, not
after, so the graph reacts the moment something happens, not once it's done.

Usage (as a hook command):
    python graph_activity_hook.py
"""
import datetime as dt
import json
import re
import sys
from pathlib import Path

LOG_PATH = Path(r"J:\Jarvis2\logs\jarvis_live_activity.jsonl")
VAULT_ROOT = Path(r"J:\Jarvis-Brain")
SCRIPTS_ROOT = Path(r"J:\Jarvis2\scripts")
APPLICATIONS_FILE = SCRIPTS_ROOT / "graph_applications.json"

TOKEN_RE = re.compile(r"[^\s\"']+")


def _known_scripts():
    return {p.name for p in SCRIPTS_ROOT.iterdir() if p.is_file() and p.suffix in (".py", ".ps1")}


def _script_to_app(scripts_by_app):
    mapping = {}
    for app_id, app in scripts_by_app.items():
        for script in app.get("scripts", []):
            mapping[script] = app_id
    return mapping


def _append(entries):
    if not entries:
        return
    LOG_PATH.parent.mkdir(exist_ok=True)
    now = dt.datetime.now().isoformat(timespec="seconds")
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        for ring, node_id in entries:
            f.write(json.dumps({"timestamp": now, "ring": ring, "node_id": node_id}, ensure_ascii=False) + "\n")


def main():
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    entries = []
    matched_script = None

    if tool_name in ("Read", "Write", "Edit"):
        file_path = tool_input.get("file_path", "")
        p = Path(file_path) if file_path else None
        if p:
            try:
                if p.suffix == ".md" and VAULT_ROOT in p.parents:
                    rel = p.relative_to(VAULT_ROOT)
                    entries.append(("memory", str(rel).replace("\\", "/")))
                elif SCRIPTS_ROOT in p.parents and p.suffix in (".py", ".ps1"):
                    matched_script = p.name
            except ValueError:
                pass  # path not relative to either root -- no match, fine

    elif tool_name in ("Bash", "PowerShell"):
        command = str(tool_input.get("command", ""))
        if command:
            known = _known_scripts()
            for token in TOKEN_RE.findall(command):
                base = Path(token).name
                if base in known:
                    matched_script = base
                    break

    if matched_script:
        entries.append(("skills", matched_script))
        try:
            apps = json.loads(APPLICATIONS_FILE.read_text(encoding="utf-8"))
            script_to_app = _script_to_app(apps)
            app_id = script_to_app.get(matched_script)
            if app_id:
                entries.append(("applications", app_id))
        except (OSError, json.JSONDecodeError):
            pass

    _append(entries)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # never break the tool call this hook is attached to
