# -*- coding: utf-8 -*-
"""Goal 2, milestone 4 (op a_4e656055, Nov 13 2026): the real loop structure for milestones
1-3 running together. Honest about what this actually is right now: milestones 1 and 2 are
real, tested, and genuinely run each cycle below. Milestone 3 (real weight-level fine-tuning)
is NOT implemented here -- it's correctly gated on real training-data volume that doesn't exist
yet (the real count climbs over time via the daemon's own auto-refill and auto-run improvement
cycle; check `swarm_meta_review.load_recent_lessons` against the real lessons file for the
current count rather than trusting a number written here, which will go stale), so
`attempt_finetune()` is a deliberate, clearly-labelled stub that returns why it didn't run rather
than faking a result.
Milestone 4 cannot be honestly called "reached" until that stub is replaced with a real
fine-tune attempt and milestone 3 is independently proven -- this module is the real scaffold
to plug that into, not the milestone itself.
"""
import swarm_meta_review as mr
import swarm_milestone3 as sm3
import swarm_self_directed_tasks as sdt

MIN_LESSONS_FOR_FINETUNE = 200


def attempt_finetune(lessons_path, min_lessons=MIN_LESSONS_FOR_FINETUNE):
    """Deliberate stub, not a placeholder pretending to be real: checks whether enough real
    training data exists, and if so, returns a clear 'not yet implemented' marker rather than
    faking a fine-tune run. Returns a dict, never silently does nothing without saying why."""
    lessons = mr.load_recent_lessons(lessons_path, n=min_lessons + 1)
    if len(lessons) < min_lessons:
        return {"attempted": False, "reason": "not enough lessons (%d/%d)" % (len(lessons), min_lessons)}
    return {"attempted": False, "reason": "enough lessons exist (%d) but the real fine-tune build is not written yet" % len(lessons)}


def run_improvement_cycle(lessons_path, field=None, config_path=sm3.CONFIG_PATH, min_lessons_for_finetune=MIN_LESSONS_FOR_FINETUNE):
    """One real cycle of the autonomous loop: milestone 1 (pattern-level self-improvement),
    then milestone 2 (self-directed practice targeting that pattern, if one was found), then
    the milestone-3 gate check. Returns a dict describing every real step -- never claims a
    step ran when it was honestly skipped. `field` defaults to None so milestone 1 classifies
    which role a detected pattern is actually about (real gap fixed 2026-11-14: every pattern
    used to get aimed at 'coder' regardless of what it was really about) -- pass an explicit
    field only to force a specific target."""
    m1 = mr.run_meta_review(lessons_path, field=field, config_path=config_path)
    m2 = None
    if m1.get("pattern_found"):
        m2 = sdt.run_self_directed_practice(m1["pattern"])
    m3 = attempt_finetune(lessons_path, min_lessons_for_finetune)
    return {"milestone1": m1, "milestone2": m2, "milestone3": m3}
