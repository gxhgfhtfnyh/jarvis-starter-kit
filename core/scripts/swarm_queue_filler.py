# -*- coding: utf-8 -*-
"""Real standing infrastructure (op a_4e656055, Nov 14 2026): hand-writing one queue job JSON
per real task was the actual bottleneck slowing down goal-2 milestone 3's data-volume problem
(28 -> 34 lessons took many manual JSON writes). This reuses swarm_self_directed_tasks's real
generation + independent-review machinery (built for milestone 2's pattern-targeted practice
tasks) with a GENERIC prompt instead of a specific failure pattern, so the queue can be kept
topped up automatically rather than one task at a time by hand.

Same integrity discipline as milestone 2, unchanged: a self-generated test suite is never
trusted until a SEPARATE role call reviews it -- this module adds bulk generation on top, not a
new, less-guarded path.
"""
import itertools
import json
import os
import uuid

import swarm_self_directed_tasks as sdt

# A real bug caught live by this module's own tests: naming queue job files from time.time()
# alone collides on rapid successive calls (coarse clock resolution, same class of issue as
# the mailbox's message-ordering bug found earlier) -- a monotonic counter plus a short random
# suffix guarantees uniqueness regardless of how fast fill_queue() is called.
_SEQ = itertools.count()

GENERIC_TASK_PROMPT = (
    "a genuinely useful, moderately challenging classic coding problem (think: algorithms, data "
    "structures, string/array manipulation) that is NOT a simple beginner exercise -- it should "
    "have at least one real, non-obvious edge case worth testing. Pick something different each "
    "time, not the most obvious textbook example."
)


def _is_valid_python(code):
    """Pure, free, deterministic -- a real gap found live: a self-generated test suite had a
    plain Python SyntaxError (an f-string with a quoting conflict) that the semantic reviewer
    approved anyway, since 'is this valid syntax' isn't the kind of thing an LLM judgment call
    reliably checks. Same lesson as the never-called-test-functions bug: a cheap mechanical
    check catches a whole class of defect no amount of semantic review will reliably catch."""
    import ast
    try:
        ast.parse(code)
        return True
    except SyntaxError:
        return False


def generate_queue_job(name):
    """Generates one real task + independently-reviewed test suite, same integrity checkpoint
    as milestone 2, plus a free syntax-validity check the semantic review alone doesn't cover.
    Returns a queue-job dict ready to write, or None if generation, the syntax check, or review
    failed (never writes an unreviewed, unparseable, or syntactically broken task to the queue)."""
    task_description, test_suite = sdt.generate_practice_task(GENERIC_TASK_PROMPT)
    if not task_description or not test_suite:
        return None
    if not _is_valid_python(test_suite):
        return None
    verdict = sdt.review_practice_task(GENERIC_TASK_PROMPT, task_description, test_suite)
    if not sdt.is_test_suite_verified(verdict):
        return None
    return {
        "name": name,
        "memory_name": "%s.txt" % name,
        "task": task_description,
        "test_suite": test_suite,
    }


def fill_queue(queue_dir, n, name_prefix="auto_gen"):
    """Generates up to n real, independently-reviewed tasks and writes them as real queue job
    files. Returns (written: int, skipped: int) -- skipped counts generation/review failures,
    never silently retried forever."""
    written = 0
    skipped = 0
    os.makedirs(queue_dir, exist_ok=True)
    for _ in range(n):
        name = "%s_%d_%s" % (name_prefix, next(_SEQ), uuid.uuid4().hex[:8])
        job = generate_queue_job(name)
        if job is None:
            skipped += 1
            continue
        path = os.path.join(queue_dir, "%s.json" % name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(job, f, indent=2)
        written += 1
    return written, skipped
