# -*- coding: utf-8 -*-
"""Bridges Kyogen's casual chat persona to the REAL swarm pipeline (op a_4e656055, Nov 14 2026).
Built right after the owner's repeated, direct point tonight: the chat "feels like im just
talking to a normal chatbot" / "its kinda dumb and cant access anything or find out anything" --
it could only talk, never actually dispatch real work. This closes that gap for real coding
requests specifically, not by weakening any safety checkpoint, but by running a chat-initiated
request through the exact same discipline self-directed tasks already use: generate a task +
test suite, have an INDEPENDENT role review the test suite is genuine (not self-graded), cross-
check the test's asserted values against an independently-generated reference implementation
(swarm_ground_truth_check, the fix from earlier tonight), and only if every checkpoint clears,
write a real job file for the daemon's own queue -- the same queue goal-2's self-directed
practice and the queue auto-refill already feed. A casual conversational request never gets a
free pass past checks a self-directed practice task has to clear.
"""
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

import swarm_ground_truth_check as gtc
import swarm_milestone1 as sm
import swarm_self_directed_tasks as sdt

QUEUE_DIR = r"C:\LocalAI\swarm_memory\queue"
RESULTS_DIR = r"C:\LocalAI\swarm_memory\results"

CODING_REQUEST_JUDGE_PROMPT = (
    "Is the following chat message a request to write, fix, or implement real code (a function, "
    "script, or small program)? Reply with exactly one word: YES or NO. Casual conversation, "
    "questions about yourself, or general chat are NO, even if they mention code or programming "
    "in passing without actually asking you to write any."
)

TASK_FROM_REQUEST_PROMPT = (
    "A user in chat asked for real code. Turn their request into a practice-task-style spec. "
    "Your reply must have exactly two sections, in this order:\n"
    "TASK:\n<a clear, concrete task description naming the exact function to implement and its "
    "signature, matching what the user actually asked for>\n"
    "TEST:\n```python\n<a complete, runnable test suite for that function, using "
    "sys.path.insert(0, r\"{code_dir}\") then `from candidate import <name>`, several real "
    "assert statements covering normal cases and at least one edge case, ending with "
    "print('ALL TESTS PASSED')>\n```\n"
    "CRITICAL FORMAT RULE, a real bug caught live 2026-11-13: write every assert as a plain "
    "top-level statement, never inside a `def test_...():` function."
)


def _call(prompt_system, user_content, max_tokens=150, timeout=60, role="kyogen_chat"):
    url, model_name = sm.ROLE_MODEL_URL.get(role, (sm.LOCAL_QWEN_URL, "qwen"))
    payload = {
        "model": model_name,
        "messages": [{"role": "system", "content": prompt_system}, {"role": "user", "content": user_content}],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="ignore").strip()
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    d = json.loads(data)
                except (ValueError, TypeError):
                    continue
                choices = d.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                piece = delta.get("content")
                if piece:
                    chunks.append(piece)
    except urllib.error.URLError as e:
        raise RuntimeError("kyogen_chat_tasks call failed: %s" % e)
    return "".join(chunks).strip()


def is_real_coding_request(user_text, max_tokens=10):
    """Calls a real judge role. Returns True only on an unambiguous YES -- never guesses a
    request is code-shaped from keyword matching (the project's own documented lesson: a real
    semantic judgment call beats a regex for this kind of classification)."""
    verdict = _call(CODING_REQUEST_JUDGE_PROMPT, user_text, max_tokens=max_tokens)
    return bool(re.match(r"^\s*YES\b", verdict, re.I))


def generate_task_from_request(user_text, max_tokens=600):
    """Same TASK:/TEST: parsing contract as swarm_self_directed_tasks.generate_practice_task.
    Returns (task_description, test_suite), either possibly None if unparseable."""
    text = _call(TASK_FROM_REQUEST_PROMPT, user_text, max_tokens=max_tokens, role="task_generator")
    task_match = re.search(r"TASK:\s*(.*?)(?=\nTEST:|\Z)", text, re.S)
    task_description = task_match.group(1).strip() if task_match else None
    test_suite = sm.extract_code(text)
    return (task_description or None), test_suite


def queue_real_chat_task(user_text, queue_dir=QUEUE_DIR, name_prefix="chat_task"):
    """The real entry point: generate -> independent review -> ground-truth cross-check -> only
    if every checkpoint clears, write a real job file for the daemon's queue. Returns a dict
    describing every step, including a 'queued' bool and the job name if queued, so the caller
    (the Discord bot) can give an honest reply either way -- never claims a task is running when
    it was actually rejected by a checkpoint."""
    task_description, test_suite = generate_task_from_request(user_text)
    if not task_description or not test_suite:
        return {"generated": False, "queued": False, "reason": "could not parse a task/test from the request"}

    verdict = sdt.review_practice_task(user_text, task_description, test_suite)
    if not sdt.is_test_suite_verified(verdict):
        return {
            "generated": True, "reviewed": True, "review_verdict": verdict, "queued": False,
            "reason": "independent review rejected the generated test suite", "task_description": task_description,
        }

    ground_truth = gtc.cross_check_test_suite(task_description, test_suite)
    if ground_truth["mismatches"]:
        return {
            "generated": True, "reviewed": True, "review_verdict": verdict, "ground_truth_check": ground_truth,
            "queued": False, "reason": "ground-truth cross-check found a mismatch", "task_description": task_description,
        }

    job_name = "%s_%s" % (name_prefix, uuid.uuid4().hex[:8])
    job = {
        "name": job_name,
        "task": task_description,
        "test_suite": test_suite,
        "source": "kyogen_chat",
        "requested_at": time.time(),
    }
    os.makedirs(queue_dir, exist_ok=True)
    with open(os.path.join(queue_dir, "%s.json" % job_name), "w", encoding="utf-8") as f:
        json.dump(job, f, indent=2)
    return {
        "generated": True, "reviewed": True, "review_verdict": verdict, "ground_truth_check": ground_truth,
        "queued": True, "job_name": job_name, "task_description": task_description,
    }


def find_result(job_name, results_dir=RESULTS_DIR):
    """Pure (one real file check). Returns the result dict once the daemon has produced it, or
    None if it hasn't finished yet -- caller polls this, never blocks waiting for it."""
    result_path = os.path.join(results_dir, "%s_result.json" % job_name)
    if not os.path.exists(result_path):
        return None
    with open(result_path, encoding="utf-8") as f:
        return json.load(f)
