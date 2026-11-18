# -*- coding: utf-8 -*-
"""Working memory layer for Kyogen (op a_4e656055, Nov 13 2026): the real gap the cognition
research surfaced -- Kyogen had per-call context (ephemeral, gone after each role call) and the
persistent swarm_memory lessons file (long-term, survives across runs), but nothing holding the
CURRENT task's active sub-goals/decisions across a role sequence within one run.

Design, from the research (see Cognitive Architecture Theories for Kyogen, Nov 13 2026):
  - A small, explicitly-capped scratch object (default cap 7, matching the human working-memory
    capacity debate's actual contested range of ~4-7), not stuffed implicitly into every prompt.
  - Read/written only by explicit calls, same as MemGPT/CoALA's in-context scratchpad pattern.
  - Explicit eviction at task end: the scratchpad gets summarized into the persistent lessons
    store (swarm_milestone2.LESSONS_PATH-style) rather than just discarded, then deleted -- real
    eviction, not an emergent decay (the research explicitly flagged human WM's decay dynamic
    has no clean equivalent here; ours is a deliberate policy, not a simulated one).
"""
import json
import os
import time

WM_DIR = r"C:\LocalAI\swarm_memory\working"
DEFAULT_CAP = 7


def _scratchpad_path(task_id, wm_dir=WM_DIR):
    return os.path.join(wm_dir, "%s.json" % task_id)


def create_scratchpad(task_id, wm_dir=WM_DIR):
    """Starts a fresh, empty scratchpad for one task run. Overwrites any existing one for the
    same task_id -- a new run starts clean, it does not accumulate across unrelated runs."""
    os.makedirs(wm_dir, exist_ok=True)
    with open(_scratchpad_path(task_id, wm_dir), "w", encoding="utf-8") as f:
        json.dump([], f)


def add_entry(task_id, entry, cap=DEFAULT_CAP, wm_dir=WM_DIR):
    """Appends one entry (a short string: an active sub-goal, a decision made, a constraint
    discovered mid-task) to the scratchpad, evicting the OLDEST entry first if already at cap.
    Creates the scratchpad if it doesn't exist yet. Returns the scratchpad after the add."""
    path = _scratchpad_path(task_id, wm_dir)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            entries = json.load(f)
    else:
        os.makedirs(wm_dir, exist_ok=True)
        entries = []
    entries.append(entry)
    while len(entries) > cap:
        entries.pop(0)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entries, f)
    return entries


def get_scratchpad(task_id, wm_dir=WM_DIR):
    """Returns the current scratchpad entries, or [] if none exists yet."""
    path = _scratchpad_path(task_id, wm_dir)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def render_for_prompt(task_id, wm_dir=WM_DIR):
    """The explicit-inclusion half of the MemGPT-style contract: the scratchpad is never
    stuffed into every prompt implicitly, a caller must explicitly ask for this string and
    choose to include it. Returns '' if the scratchpad is empty (nothing to add)."""
    entries = get_scratchpad(task_id, wm_dir)
    if not entries:
        return ""
    return "CURRENT TASK WORKING MEMORY:\n" + "\n".join("- %s" % e for e in entries)


def evict_to_long_term(task_id, lessons_path, wm_dir=WM_DIR, now=None, vault_path=None):
    """Real eviction at task end: summarizes the scratchpad (if non-empty) into the persistent
    lessons store as one combined entry, then deletes the scratchpad file. A deliberate policy,
    not simulated decay. Returns True if something was actually evicted, False if the
    scratchpad was already empty/missing (nothing to evict)."""
    entries = get_scratchpad(task_id, wm_dir)
    path = _scratchpad_path(task_id, wm_dir)
    if not entries:
        if os.path.exists(path):
            os.remove(path)
        return False
    summary = "From task '%s': %s" % (task_id, "; ".join(entries))
    os.makedirs(os.path.dirname(lessons_path), exist_ok=True)
    with open(lessons_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"lesson": summary, "ts": now or time.time(), "source": "working_memory_eviction"}) + "\n")
    # Also writes the same real eviction into the richer, categorized, linkable vault (Goal 2,
    # 2026-11-14: "shouldnt we give it a proper memory vault vs just short term memory") --
    # additive only, lessons.jsonl keeps writing exactly as before for every existing reader.
    # category="general" here, not a synchronous judge call: classifying every single task
    # eviction would add a real model call (and real latency) to every task completion;
    # kyogen_memory_vault.classify_memory_category exists for an idle-time pass to upgrade
    # these later instead.
    import kyogen_memory_vault as kv
    kv.add_memory(summary, category="general", source=task_id, now=now, vault_path=vault_path or kv.VAULT_PATH)
    os.remove(path)
    return True
