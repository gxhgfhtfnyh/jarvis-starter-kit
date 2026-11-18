# -*- coding: utf-8 -*-
"""Status reporting for Archive Terminal actions (the ACTION button on cards, files, projects and quizzes).

    python action_update.py list [N]                       # recent actions, newest first
    python action_update.py <id> working                   # I have started
    python action_update.py <id> acknowledged              # I have seen it
    python action_update.py <id> done "one-line result"    # finished (what changed, where)
    python action_update.py <id> failed "why"              # could not do it
    python action_update.py <id> blocked "what I need from the owner"

done / failed / blocked on a CARD operation are refused until its file holds at least 3 real steps (op_note.py --kind step) and one
interim progress update (op_note.py --stdin --kind update, 20+ words); add --thin to stamp anyway - the report is then labelled THIN.

Optional progress (any status, or on its own with no status):
    --pct N          progress 0-100 (clamped); shown as the percent and the bar in ACTION LOG and the tracker chip
    --note "text"    one timeline line (max 160 chars, newest last, 20 kept) shown under the job in ACTION LOG
    python action_update.py <id> working --pct 40 --note "read the note, drafting the fix"
    python action_update.py <id> --pct 75 --note "tests running"      # status unchanged
    python action_update.py <id> done "summary" --note "verified"       # done also sets progress 100 unless --pct is given

The Terminal UI shows these as the SENT / ACKNOWLEDGED / WORKING / DONE / FAILED / BLOCKED chips and in ACTION LOG.
The store is a JSON file (ARCHIVE_ACTIONS_FILE, default J:\\Jarvis2\\logs\\ui_actions.json) shared with the UI backend
(features_py/actions.py in the jarvis-ui repo); writes are atomic and serialised with a lock file.
"""
import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

FILE = Path(os.environ.get("ARCHIVE_ACTIONS_FILE") or r"J:\Jarvis2\logs\ui_actions.json")
STATUSES = ("acknowledged", "working", "done", "failed", "blocked")
OPEN = ("sent", "acknowledged", "working")
MAX_SUMMARY = 400
MAX_NOTE = 160
MAX_UPDATES = 20


def _load():
    try:
        data = json.loads(FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("actions"), list) else {"actions": []}
    except (OSError, ValueError):
        return {"actions": []}


class _Lock:
    """A crude cross-process lock: an exclusively created sidecar file, retried briefly, stale after 10 s."""
    def __init__(self, path):
        self.path = path.with_name(path.name + ".lock")

    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 10:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("actions file is locked")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _clean(text):
    return " ".join("".join(ch if ch >= " " and ch != "\x7f" else " " for ch in str(text)).split())[:MAX_SUMMARY]


def _now():
    return dt.datetime.now().isoformat(timespec="seconds")


def parse_pct(value):
    """'40' / 40 / '40%' -> int 0-100, or None when it is not a plain number."""
    try:
        text = str(value).strip().rstrip("%").strip()
        number = float(text)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return max(0, min(100, int(round(number))))


def update(action_id, status, summary="", pct=None, note=""):
    """status may be None/'' to leave it unchanged (progress-only update)."""
    if status and status not in STATUSES:
        return False, f"status must be one of {', '.join(STATUSES)}"
    if not status and pct is None and not note:
        return False, "nothing to update (give a status, --pct or --note)"
    with _Lock(FILE):
        data = _load()
        for a in data["actions"]:
            if a.get("id") == action_id:
                if a.get("status") == "cancelled":
                    return False, "that action was cancelled in the UI"
                stamp = _now()
                if status:
                    a["status"] = status
                if pct is not None:
                    a["progress"] = pct
                elif status == "done":
                    a["progress"] = 100
                a["updated"] = stamp
                if summary:
                    a["summary"] = _clean(summary)
                text = _clean(note)[:MAX_NOTE]
                if text:
                    updates = a.get("updates") if isinstance(a.get("updates"), list) else []
                    updates = [u for u in updates if isinstance(u, dict)]
                    updates.append({"ts": stamp, "text": text})
                    a["updates"] = updates[-MAX_UPDATES:]
                FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = FILE.with_name(FILE.name + ".tmp")
                tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                tmp.replace(FILE)
                bits = [f"{action_id} -> {a['status']}"]
                if pct is not None or status == "done":
                    bits.append(f"{a['progress']}%")
                if text:
                    bits.append("note added")
                return True, " ".join(bits)
    return False, f"unknown action id: {action_id}"


def _substance_gate(action_id, status):
    """done / blocked / failed need a real paper trail on the operation's file. Returns '' when fine, else the refusal text."""
    if status not in ("done", "failed", "blocked"):
        return ""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from op_note import load_op_files
        mod = load_op_files()
        if mod is None or not hasattr(mod, "substance_check"):
            return ""
        ok, why = mod.substance_check(action_id)
        return "" if ok else why
    except Exception:
        return ""


def _file_on_operation(action_id, status, summary, note, thin=False):
    """Mirrors the update onto the card's operation FILE (op_files.py in the jarvis-ui repo): the note becomes a filed
    entry, and done / blocked / failed stamps the file and writes its OPERATION REPORT. Best effort: never fails the update."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from op_note import load_op_files
        mod = load_op_files()
        if mod is None:
            return ""
        done, info = mod.record_status(action_id, status or "", summary, _clean(note)[:MAX_NOTE] if note else "", thin=thin)
        if not done or info in ("not a card action", "nothing to record"):
            return ""
        extra = ""
        if "auto-built" in info:
            extra += " | report auto-built from the logged steps (file a full one first next time: op_report.py)"
        if "\nNEW NOTE" in info:  # the owner added notes to this operation since I last looked: show them (fenced as data)
            extra += info[info.index("\nNEW NOTE"):]
        return " | operation file updated" + extra
    except Exception as e:
        return f" | operation file NOT updated ({type(e).__name__})"


def _archive_priority_line(action_id):
    """2026-10-29: a done operation's Open Tasks line in Active Priorities (read at every startup) was never moved, so the file grew about 500 bytes per
    operation. Move it to the Completed Priorities Archive. Best effort, never fails the update."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import add_priority
        title = ""
        for a in _load().get("actions", []):
            if a.get("id") == action_id:
                title = str(a.get("title") or "")
        if title and add_priority.close_task(title):
            return " | its Active Priorities line moved to the archive"
    except Exception as e:
        return " | (priority line not moved: %s)" % type(e).__name__
    return ""


def split_args(args):
    """Pulls --pct N / --note TEXT (also --pct=N) out of args. Returns (rest, pct, note, error)."""
    rest, pct, note, i = [], None, "", 0
    while i < len(args):
        arg = args[i]
        name, eq, inline = arg.partition("=")
        if name in ("--pct", "--note"):
            if eq:
                value = inline
            elif i + 1 < len(args):
                i += 1
                value = args[i]
            else:
                return rest, pct, note, f"{name} needs a value"
            if name == "--pct":
                pct = parse_pct(value)
                if pct is None:
                    return rest, pct, note, "--pct must be a number 0-100"
            else:
                note = value
        else:
            rest.append(arg)
        i += 1
    return rest, pct, note, None


def main(argv):
    if len(argv) >= 2 and argv[1] == "list":
        n = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 10
        for a in reversed(_load()["actions"][-n:]):
            pct = f" {a['progress']}%" if isinstance(a.get("progress"), int) else ""
            print(f"{a.get('id')}  {a.get('status'):<12} {a.get('kind')}: {a.get('title')}{pct}  {a.get('summary', '')}")
        return 0
    thin = "--thin" in argv[1:]
    rest, pct, note, error = split_args([a for a in argv[1:] if a != "--thin"])
    if error:
        print(error)
        return 1
    if not rest or (len(rest) < 2 and pct is None and not note):
        print(__doc__)
        return 1
    action_id = rest[0]
    if len(rest) >= 2 and rest[1] in STATUSES:
        status, summary = rest[1], " ".join(rest[2:])
    elif len(rest) >= 2 and rest[1].startswith("-"):
        status, summary = "", " ".join(rest[2:])
    elif len(rest) >= 2:
        status, summary = rest[1], " ".join(rest[2:])  # invalid status: update() reports it
    else:
        status, summary = "", ""
    if not thin:
        refusal = _substance_gate(action_id, status)
        if refusal:
            print(refusal)
            return 1
    ok, message = update(action_id, status, summary, pct, note)
    if ok:
        message += _file_on_operation(action_id, status, summary, note, thin=thin)
        if status == "done":
            message += _archive_priority_line(action_id)
        if note and len(note.split()) < 12:
            message += (" | WARNING: that note is only " + str(len(note.split())) + " words. File a real step and progress report instead: "
                        "op_note.py " + action_id + ' "<what you did>" --kind step --target "<files>" --result "<outcome>"')
    print(message)
    return 0 if ok else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv))
