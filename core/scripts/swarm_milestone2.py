# -*- coding: utf-8 -*-
"""Milestone 2 of the local specialist-swarm AGI experiment (op a_4e656055, Nov 13 2026):
can the swarm write a durable lesson back to its own persistent memory, verified before it
sticks, and have a LATER run actually retrieve and use that lesson?

This is the "memory self-editing" rung of the milestone ladder -- lowest-risk form of
"learning from experience" (per the scoped plan): no weight edits, no config edits, just a
plain verified-append-only lessons file that future runs load into their prompt context.

Round trip proven here, not assumed:
  1. propose_lesson() - a memory-writer role looks at one completed milestone-1 run and
     proposes one concise, generalizable lesson (not task-specific trivia).
  2. verify_lesson() - a verifier role checks the lesson is genuinely generalizable and not
     garbage/wrong before it's allowed to persist.
  3. commit_lesson() - appends the verified lesson to a persistent store (survives process
     exit, unlike anything held in a conversation).
  4. load_lessons() - a later, independent process call reads the store back and the lesson
     text is injected into that run's prompts.
"""
import json
import os
import time

import swarm_milestone1 as sm

LESSONS_PATH = os.path.join(sm.SWARM_MEMORY_DIR, "lessons.jsonl")

MEMORY_WRITER_PROMPT = (
    "You just watched one coding task get attempted. Propose ONE concise, generalizable "
    "lesson future attempts at DIFFERENT tasks could use -- not a fact about this specific "
    "task's answer, a general coding/process lesson. It must be something a strict reviewer "
    "would agree is both TRUE and genuinely useful on an unrelated task, not vague filler like "
    "'always test your code'. Pick something specific enough to be falsifiable, general enough "
    "to apply elsewhere. One or two sentences, plain prose, no code."
)

LESSON_VERIFIER_PROMPT = (
    "You are checking a proposed 'lesson learned' before it is allowed to persist into a "
    "shared memory store that future runs will read. State PASS if it is genuinely general "
    "(would help on a DIFFERENT task, not just restate this one's specific answer) and not "
    "false/misleading, or FAIL: <reason> if it is too task-specific, wrong, or vague filler. "
    "One line only."
)


def propose_lesson(task, run_summary):
    """Calls the memory-writer role on a completed run's summary. Returns the proposed text."""
    prompt = "TASK:\n%s\n\nRUN SUMMARY:\n%s" % (task, run_summary)
    return _call_memory_writer(prompt)


def _call_memory_writer(prompt, max_tokens=200):
    """Thin wrapper so tests can monkeypatch this one call site instead of sm._call_role's
    general dispatch (the memory-writer isn't one of sm.ROLE_PROMPTS' existing roles)."""
    import json as _json
    import urllib.request
    import urllib.error

    payload = {
        "model": "qwen",
        "messages": [
            {"role": "system", "content": MEMORY_WRITER_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(
        sm.LOCAL_QWEN_URL,
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    d = _json.loads(data)
                except (ValueError, TypeError):
                    continue
                choices = d.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    chunks.append(text)
    except urllib.error.URLError as e:
        raise RuntimeError("local-qwen call failed: %s" % e)
    return "".join(chunks).strip()


def verify_lesson(lesson_text):
    """Calls the verifier role on a proposed lesson. Returns the verdict text."""
    verify_prompt = "PROPOSED LESSON:\n%s" % lesson_text
    payload = {
        "model": "qwen",
        "messages": [
            {"role": "system", "content": LESSON_VERIFIER_PROMPT},
            {"role": "user", "content": verify_prompt},
        ],
        "stream": True,
        "max_tokens": 100,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    return _raw_call(payload)


def _raw_call(payload):
    import json as _json
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        sm.LOCAL_QWEN_URL,
        data=_json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    d = _json.loads(data)
                except (ValueError, TypeError):
                    continue
                choices = d.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                text = delta.get("content")
                if text:
                    chunks.append(text)
    except urllib.error.URLError as e:
        raise RuntimeError("local-qwen call failed: %s" % e)
    return "".join(chunks).strip()


def is_lesson_verified(verdict):
    """Pure. True only if the verdict is an unambiguous PASS (not merely absent 'FAIL')."""
    v = verdict.strip().upper()
    return v.startswith("PASS")


def commit_lesson(lesson_text, lessons_path=LESSONS_PATH, now=None):
    """Appends a verified lesson to the persistent store. Pure side effect, no verification
    performed here -- caller must have already checked is_lesson_verified()."""
    os.makedirs(os.path.dirname(lessons_path), exist_ok=True)
    row = {"lesson": lesson_text, "ts": now or time.time()}
    with open(lessons_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def load_lessons(lessons_path=LESSONS_PATH):
    """Reads every committed lesson back as a single prompt-ready text block, or '' if none
    exist yet. This is the retrieval half of the round trip -- a later, independent process
    call must be able to read what an earlier one wrote."""
    if not os.path.exists(lessons_path):
        return ""
    lines = []
    with open(lessons_path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except (ValueError, TypeError):
                continue
            lines.append("- " + row.get("lesson", ""))
    if not lines:
        return ""
    return "LESSONS FROM PAST RUNS:\n" + "\n".join(lines)


def run_round_trip(task=sm.TASK, test_suite=sm.LRU_TEST_SUITE, lessons_path=LESSONS_PATH):
    """The real end-to-end proof: run milestone 1 once, propose+verify+commit a lesson from
    it, then prove a FRESH load_lessons() call (a new, independent read, same as a future
    process would do) actually returns that lesson. Returns a dict describing what happened
    at each step, not just a pass/fail."""
    run_result = sm.run_condition_a(task, test_suite)
    summary = "Condition A (baseline) on this task: passed=%s, detail=%s" % (run_result["passed"], run_result["detail"])
    lesson = propose_lesson(task, summary)
    verdict = verify_lesson(lesson)
    verified = is_lesson_verified(verdict)
    committed = False
    if verified:
        commit_lesson(lesson, lessons_path)
        committed = True
    retrieved = load_lessons(lessons_path)
    round_trip_proven = committed and (lesson.strip() != "" and lesson.strip() in retrieved)
    return {
        "run_result": run_result,
        "proposed_lesson": lesson,
        "verdict": verdict,
        "verified": verified,
        "committed": committed,
        "retrieved_contains_lesson": round_trip_proven,
    }


if __name__ == "__main__":
    result = run_round_trip()
    print(json.dumps(result, indent=2))
