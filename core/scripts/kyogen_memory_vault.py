# -*- coding: utf-8 -*-
"""Kyogen's real structured memory vault (Goal 2, op a_4e656055), built 2026-11-14 after
the owner: "shouldnt we give it a proper memory vault vs just short term memory" -- a fair, real
gap. `lessons.jsonl` (swarm_working_memory.evict_to_long_term) is a flat list of plain-text
summary strings: real, accumulated, but not categorized and not linkable to related entries,
unlike Jarvis's own vault (folders, categories, [[links]] between notes). This module is that
same idea scaled down to what Kyogen's own pipeline can realistically populate: every real entry
has a category (which role/aspect of the system it's actually about) and an explicit list of
related entry ids, so later work can follow a real thread instead of re-reading a flat log start
to finish. Deliberately separate from lessons.jsonl, not a replacement -- existing code that
reads lessons.jsonl (the persona's recent-experience summary, meta-review's pattern mining)
keeps working unchanged; this is an additional, richer store written alongside it.
"""
import json
import os
import time
import uuid

VAULT_PATH = r"C:\LocalAI\swarm_memory\vault.jsonl"

# Same four real pipeline roles swarm_meta_review.classify_pattern_field already uses, plus two
# broader buckets for memory that isn't about one specific pipeline role.
CATEGORIES = ("coder", "researcher", "verifier", "self_confidence", "persona", "general")

CLASSIFY_CATEGORY_PROMPT = (
    "Which ONE category does this memory entry belong to: coder, researcher, verifier, "
    "self_confidence, persona, or general? 'coder' is about writing/generating code, "
    "'researcher' is about analysis or fact-finding, 'verifier' is about judging/reviewing "
    "code or test suites, 'self_confidence' is about a role's own confidence rating, "
    "'persona' is about Kyogen's own conversational identity/chat behavior, 'general' is "
    "anything else. Reply with exactly one of those six words."
)


def new_memory(content, category="general", links=None, source=None, now=None):
    """Pure. Builds one real memory entry dict -- never writes it. A real id (uuid4 hex) so
    other entries can reference it via `links` regardless of write order."""
    cat = category if category in CATEGORIES else "general"
    return {
        "id": uuid.uuid4().hex[:12],
        "category": cat,
        "content": content,
        "links": list(links) if links else [],
        "source": source,
        "ts": now if now is not None else time.time(),
    }


def add_memory(content, category="general", links=None, source=None, vault_path=VAULT_PATH, now=None):
    """Appends one real memory entry to the vault (JSONL, same append-only convention as
    lessons.jsonl) and returns the entry that was written."""
    entry = new_memory(content, category, links, source, now)
    os.makedirs(os.path.dirname(vault_path), exist_ok=True)
    with open(vault_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def load_all(vault_path=VAULT_PATH):
    """Returns every real entry in the vault, oldest first, or [] if the vault doesn't exist
    yet. Skips a corrupt line rather than crashing (same discipline as swarm_meta_review's
    lessons loader) -- one bad line should never take down every later read."""
    if not os.path.exists(vault_path):
        return []
    entries = []
    with open(vault_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
    return entries


def query_by_category(category, vault_path=VAULT_PATH, n=None):
    """Returns real entries in the given category, oldest first, capped to the most recent n
    if given. Never fabricates entries for a category with nothing real in it -- returns []."""
    matches = [e for e in load_all(vault_path) if e.get("category") == category]
    return matches[-n:] if n else matches


def search(keyword, vault_path=VAULT_PATH):
    """Case-insensitive real substring search across every entry's content. Pure text search,
    not semantic -- an honest, simple first version, not a vector index."""
    needle = keyword.lower()
    return [e for e in load_all(vault_path) if needle in e.get("content", "").lower()]


def get_linked(entry_id, vault_path=VAULT_PATH):
    """Real bidirectional link traversal: entries the given entry points to (outward,
    `links` contains entry_id's... no -- entries THIS entry's own links list names) plus
    entries that point back at it (inward, whose own links list contains entry_id). Returns []
    for an id that doesn't exist or has no real connections either direction."""
    entries = load_all(vault_path)
    by_id = {e["id"]: e for e in entries}
    this = by_id.get(entry_id)
    outward = [by_id[lid] for lid in (this.get("links", []) if this else []) if lid in by_id]
    inward = [e for e in entries if e["id"] != entry_id and entry_id in e.get("links", [])]
    seen = set()
    result = []
    for e in outward + inward:
        if e["id"] not in seen:
            seen.add(e["id"])
            result.append(e)
    return result


def classify_memory_category(content, max_tokens=10):
    """A real judge-call classifier (same discipline as swarm_meta_review.classify_pattern_field
    -- no keyword matching), for callers that want a better category than the cheap default
    (e.g. an idle-time consolidation pass re-classifying 'general' entries), not required on the
    hot write path where a synchronous model call per entry would add real latency to every
    single task completion. Falls back to 'general' on a malformed reply, never crashes the
    caller over a bad classification."""
    import swarm_meta_review as mr
    url, model_name = mr.sm.ROLE_MODEL_URL.get("meta_reviewer", (mr.sm.LOCAL_QWEN_URL, "qwen"))
    verdict = mr._call_with_system_prompt(url, model_name, CLASSIFY_CATEGORY_PROMPT, content, max_tokens)
    cleaned = verdict.strip().lower()
    for cat in CATEGORIES:
        if cat in cleaned:
            return cat
    return "general"


def reclassify_general_entries(vault_path=VAULT_PATH, max_entries=5):
    """The real idle-time pass `classify_memory_category`'s own docstring promised: finds up to
    `max_entries` real 'general' entries (oldest first, so the backlog actually drains over
    successive idle ticks rather than reclassifying the same few newest ones forever) and
    upgrades each to its real category via the real judge call. Rewrites the whole file (JSONL
    has no cheap in-place edit) -- every entry's id/content/links/source/ts is preserved
    exactly, only `category` changes on the reclassified ones. Returns the number of entries
    actually reclassified (0 if there were none left to do, never fabricated)."""
    entries = load_all(vault_path)
    general_ids = [e["id"] for e in entries if e.get("category") == "general"][:max_entries]
    if not general_ids:
        return 0
    by_id = {e["id"]: e for e in entries}
    for eid in general_ids:
        by_id[eid]["category"] = classify_memory_category(by_id[eid]["content"])
    with open(vault_path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return len(general_ids)
