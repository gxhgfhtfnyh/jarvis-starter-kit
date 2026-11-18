# -*- coding: utf-8 -*-
"""Goal 2, milestone 2 (op a_4e656055, Nov 13 2026): self-directed task generation. Depends on
milestone 1 (swarm_meta_review.py) existing first -- a recurring failure pattern mined from
real accumulated lessons is the "skill gap" this module targets, not an invented one. The
system generates its OWN practice task aimed at that gap, runs it through the real pipeline, and
scores itself -- closing the loop without me or him picking what to test next.

Real integrity risk, handled deliberately rather than ignored: if the SAME model both writes a
practice task and writes the test suite that grades it, a trivial or tautological test (e.g.
`assert True`) would give false confidence. review_practice_task() is a second, independent
role call that must confirm the test suite genuinely enforces the stated failure pattern before
the practice task is ever actually run for real -- the same checkpoint discipline the rest of
this project already uses (an independent verifier, not self-grading).
"""
import re

import swarm_coordination as coord
import swarm_ground_truth_check as gtc
import swarm_milestone1 as sm

TASK_GENERATOR_PROMPT = (
    "You design practice coding tasks that specifically exercise a named failure pattern. Your "
    "reply must have exactly two sections, in this order:\n"
    "TASK:\n<a clear, concrete task description naming the exact function to implement and its "
    "signature, small enough to solve in a few lines>\n"
    "TEST:\n```python\n<a complete, runnable test suite for that function, using "
    "sys.path.insert(0, r\"{code_dir}\") then `from candidate import <name>`, several real "
    "assert statements that specifically probe the named failure pattern (not just a basic "
    "happy-path check), ending with print('ALL TESTS PASSED')>\n```\n"
    "The test suite must be a genuine, non-trivial check -- never a test that would pass for "
    "obviously broken code. CRITICAL FORMAT RULE, a real bug caught live 2026-11-13: write every "
    "assert as a plain top-level statement, never inside a `def test_...():` function -- a test "
    "suite that only DEFINES test functions without ever calling them will execute with zero "
    "assertions actually run, silently proving nothing while looking complete."
)

TEST_SUITE_REVIEW_PROMPT = (
    "You are reviewing a self-generated test suite for a self-generated practice task, before "
    "it is trusted. Given the named failure pattern, the task description, and the test suite "
    "code, state PASS only if the test suite is a genuine, non-trivial check that would "
    "actually fail on code exhibiting the named failure pattern -- or FAIL: <reason> if the "
    "test suite is trivial, tautological, too easy to pass vacuously, or does not actually test "
    "the named pattern. One line only."
)


def generate_practice_task(pattern, max_tokens=600):
    """Calls the real task-generator role. Returns (task_description, test_suite) -- either
    may be None if the reply didn't contain a parseable section (never guesses a task or test
    suite out of an unparseable reply)."""
    prompt = "FAILURE PATTERN TO TARGET:\n%s" % pattern
    text = _call_task_generator(prompt, max_tokens)
    task_match = re.search(r"TASK:\s*(.*?)(?=\nTEST:|\Z)", text, re.S)
    task_description = task_match.group(1).strip() if task_match else None
    test_suite = sm.extract_code(text)
    return (task_description or None), test_suite


def _call_task_generator(prompt, max_tokens):
    url, model_name = sm.ROLE_MODEL_URL.get("task_generator", (sm.LOCAL_QWEN_URL, "qwen"))
    return _call_with_system_prompt(url, model_name, TASK_GENERATOR_PROMPT, prompt, max_tokens)


def review_practice_task(pattern, task_description, test_suite, max_tokens=100):
    """The integrity checkpoint: an independent role call judges whether the self-generated test
    suite is a real, non-trivial check, not self-graded by the same role that wrote it. Returns
    the verdict text."""
    prompt = "FAILURE PATTERN:\n%s\n\nTASK:\n%s\n\nTEST SUITE:\n%s" % (pattern, task_description, test_suite)
    url, model_name = sm.ROLE_MODEL_URL.get("verifier", (sm.LOCAL_QWEN_URL, "qwen"))
    return _call_with_system_prompt(url, model_name, TEST_SUITE_REVIEW_PROMPT, prompt, max_tokens)


def is_test_suite_verified(verdict):
    """Pure. True only on an unambiguous PASS, same contract as milestone 3's is_edit_verified."""
    return bool(re.match(r"^\s*PASS\b", verdict or "", re.I))


def _call_with_system_prompt(url, model_name, system_prompt, user_content, max_tokens):
    import json
    import urllib.error
    import urllib.request

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    chunks = []
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
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
                text = delta.get("content")
                if text:
                    chunks.append(text)
    except urllib.error.URLError as e:
        raise RuntimeError("self-directed-task call failed: %s" % e)
    return "".join(chunks)


def run_self_directed_practice(pattern):
    """The real entry point: generate a practice task for the given failure pattern, have an
    independent role review the self-generated test suite before trusting it, cross-check its
    asserted expected VALUES against an independently-generated reference implementation (a
    real bug caught live 2026-11-13: the semantic reviewer approved a test whose expected value
    was simply wrong -- "is this a genuine test of the right concept" and "is this specific
    number actually correct" are different questions, so this is a third, mechanically-different
    checkpoint, not another LLM judgment layered on the same question), and only if every
    checkpoint clears, actually run the full real coder+verifier pipeline against it. Returns a
    dict describing every step -- never silently skips a checkpoint."""
    task_description, test_suite = generate_practice_task(pattern)
    if not task_description or not test_suite:
        return {"generated": False, "reviewed": False, "ran": False}
    verdict = review_practice_task(pattern, task_description, test_suite)
    if not is_test_suite_verified(verdict):
        return {
            "generated": True, "reviewed": True, "review_verdict": verdict, "ran": False,
            "task_description": task_description, "test_suite": test_suite,
        }
    ground_truth = gtc.cross_check_test_suite(task_description, test_suite)
    if ground_truth["mismatches"]:
        return {
            "generated": True, "reviewed": True, "review_verdict": verdict,
            "ground_truth_check": ground_truth, "ran": False,
            "task_description": task_description, "test_suite": test_suite,
        }
    pipeline_result = _run_practice_pipeline(task_description, test_suite)
    return {
        "generated": True, "reviewed": True, "review_verdict": verdict,
        "ground_truth_check": ground_truth, "ran": True,
        "task_description": task_description, "test_suite": test_suite,
        "pipeline_result": pipeline_result,
    }


def _run_practice_pipeline(task_description, test_suite):
    """Runs the real specialist+verifier pipeline (same as any real queued task) against the
    self-generated, independently-reviewed practice task. A throwaway memory file -- practice
    runs are not real production tasks and should not collide with a real job's memory file."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        memory_path = os.path.join(d, "practice.txt")
        return coord.run_task_for_daemon(memory_path, task_description, test_suite)
