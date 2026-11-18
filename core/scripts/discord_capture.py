# -*- coding: utf-8 -*-
"""
Task capture over Discord: the owner DMs "add: renew passport" and it lands as
an open task in Active Priorities.md (via add_priority.add_open_task).

Pure/mechanical: no network, no URL fetching, no model calls. Only the owner's
own user id, in a DM, can trigger it -- everyone else (friends, guild
channels, bots) returns None and is handled exactly as before.
"""
import re
import threading

import add_priority

MAX_TITLE = 200
_PREFIX = re.compile(r"^\s*(?:add|task|todo):[ ]?", re.IGNORECASE)
_lock = threading.Lock()


def _clean(title):
    t = re.sub(r"\s+", " ", title).strip()
    prev = None
    while prev != t:  # loop: removal can splice a new '<!--' together
        prev = t
        t = t.replace("<!--", "").replace("-->", "").replace("**", "").replace("__", "")
    t = t.replace("`", "'")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"\[\[|\]\]", "", t)  # wiki-link brackets
    # leading structural tokens (repeat until stable): '#', '- [ ]', '- ', '* ', '>', '1.'
    prev = None
    while prev != t:
        prev = t
        t = re.sub(r"^(?:#+|-\s*\[[ xX]?\]|[-*+>]|\d+[.)])\s*", "", t).strip()
    return t.strip()


def parse_capture(text):
    """None if not a capture; {'title': str} otherwise (or None if title is
    empty/unusable after cleaning)."""
    if not isinstance(text, str):
        return None
    m = _PREFIX.match(text)
    if not m:
        return None
    title = _clean(text[m.end():])
    if not title or len(title) > MAX_TITLE:
        return None
    return {"title": title}


def _norm(s):
    s = re.sub(r"[*_`]", "", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def _open_titles(content):
    marker = "### Open Tasks\n"
    if marker not in content:
        return []
    rest = content[content.index(marker) + len(marker):]
    out = []
    for line in rest.split("\n"):
        if line.startswith("#"):
            break
        m = re.match(r"^- \[ \] (.*)$", line)
        if m:
            out.append(_norm(m.group(1)))
    return out


def add_card(title, today=None, path=None):
    """Append via add_priority.add_open_task. `today` is accepted for API
    symmetry but unused (task format has no date). `path` overrides the
    priorities file (tests)."""
    old = add_priority.ACTIVE_PRIORITIES_PATH
    with _lock:
        try:
            if path is not None:
                add_priority.ACTIVE_PRIORITIES_PATH = path
            content = add_priority.ACTIVE_PRIORITIES_PATH.read_text(encoding="utf-8")
            if _norm(title) in _open_titles(content):
                return {"ok": False, "reason": "DUPLICATE", "title": title}
            add_priority.add_open_task(title)
            return {"ok": True, "title": title}
        except Exception as e:  # never raise into the daemon
            return {"ok": False, "reason": f"{type(e).__name__}: {e}", "title": title}
        finally:
            add_priority.ACTIVE_PRIORITIES_PATH = old


def handle_message(author_id, text, owner_id, is_dm, path=None):
    """Reply text, or None meaning 'not a capture, handle normally'."""
    if not is_dm or author_id != owner_id:
        return None
    if not isinstance(text, str) or not _PREFIX.match(text):
        return None
    parsed = parse_capture(text)
    if parsed is None:
        return "couldn't add that: empty or too long (max 200 chars)"
    res = add_card(parsed["title"], None, path=path)
    if res["ok"]:
        return f"added: {res['title']}"
    if res["reason"] == "DUPLICATE":
        return f"already on the list: {res['title']}"
    return f"couldn't add that: {res['reason']}"
