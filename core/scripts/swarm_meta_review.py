# -*- coding: utf-8 -*-
"""Goal 2, milestone 1 (op a_4e656055, Nov 13 2026): pattern-level self-improvement from
accumulated experience. Until now, every lesson in lessons.jsonl is written and later retrieved
in isolation -- nothing looks ACROSS lessons for a recurring pattern. This module is that
missing pass: read a batch of recent real lessons, ask a model to find ONE genuinely recurring
failure pattern (one that shows up across at least 2 distinct tasks, not a one-off), and if
found, feed it as the "reason" into milestone 3's already-verified propose -> verify -> apply ->
rollback config-editing pipeline. The difference from milestone 3 itself is entirely about WHAT
triggers a proposal: a pattern mined from real task history, not a human reacting to one
incident.

Safety: reuses milestone 3's exact verified/reversible mechanism unchanged -- a detected
pattern still only becomes a real config change after an independent verifier PASS, and every
apply keeps a timestamped backup. This module adds pattern-detection on top, not a new,
less-guarded way to edit the swarm's own config.
"""
import json

import swarm_milestone1 as sm
import swarm_milestone3 as sm3

META_REVIEW_PROMPT = (
    "You will be given several short lesson summaries from past coding tasks, each from a "
    "different task. Look ACROSS all of them for ONE genuinely recurring failure pattern -- "
    "something that shows up in at least 2 of the summaries, not a one-off specific to a single "
    "task. If you find one, reply with ONE line: a concise, general description of the pattern "
    "(not tied to any single task's specifics). If no clear recurring pattern exists across at "
    "least 2 of them, reply with exactly the word NONE and nothing else. Be honest -- NONE is a "
    "correct, useful answer when the lessons are not actually a shared pattern."
)

FIELD_CLASSIFIER_PROMPT = (
    "A recurring failure pattern was found in a coding pipeline with four roles: coder (writes "
    "the implementation), researcher (lists edge cases before code is written), verifier "
    "(reviews the code and approves or rejects it), self_confidence (the coder's own self-rating "
    "of its code). Given the pattern description, which ONE role's own prompt/instructions "
    "should actually be changed to fix it at the root? Reply with exactly one word: coder, "
    "researcher, verifier, or self_confidence."
)
VALID_FIELDS = ("coder", "researcher", "verifier", "self_confidence")


def load_recent_lessons(lessons_path, n=20):
    """Pure-ish (one real file read). Returns up to the last n lesson dicts, oldest first
    within that window. Empty list if the file doesn't exist yet."""
    try:
        with open(lessons_path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    return [json.loads(line) for line in lines[-n:]]


def find_recurring_pattern(lessons, max_tokens=150):
    """Calls the real meta-review role on a batch of lesson texts. Returns the pattern
    description, or None if fewer than 2 lessons were given, the model said NONE, or its reply
    was empty -- never guesses a pattern out of insufficient data."""
    if len(lessons) < 2:
        return None
    batch = "\n".join("- %s" % l.get("lesson", "")[:300] for l in lessons)
    prompt = "LESSON SUMMARIES:\n%s" % batch
    url, model_name = sm.ROLE_MODEL_URL.get("meta_reviewer", (sm.LOCAL_QWEN_URL, "qwen"))
    text = _call_meta_reviewer(url, model_name, prompt, max_tokens)
    text = text.strip()
    if not text or text.upper().startswith("NONE"):
        return None
    return text


def _call_meta_reviewer(url, model_name, prompt, max_tokens):
    """Direct call using META_REVIEW_PROMPT -- its own role, not a reuse of any existing
    ROLE_PROMPTS entry, same pattern as swarm_arbitration's _call_scoring."""
    import urllib.error
    import urllib.request

    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": META_REVIEW_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
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
        raise RuntimeError("meta-review call failed: %s" % e)
    return "".join(chunks)


def classify_pattern_field(pattern, max_tokens=10):
    """Real gap found live 2026-11-14: run_meta_review always proposed fixes to the CODER's
    prompt specifically, regardless of what the detected pattern was actually about -- a
    pattern genuinely about the verifier being too lenient, or the researcher missing edge
    cases, still only ever got aimed at coder's config. Calls a real judge role instead of
    guessing or hardcoding. Returns 'coder' as a safe, validated fallback if the model's reply
    isn't one of the real valid fields -- never lets a malformed reply target an arbitrary
    config key."""
    url, model_name = sm.ROLE_MODEL_URL.get("meta_reviewer", (sm.LOCAL_QWEN_URL, "qwen"))
    text = _call_with_system_prompt(url, model_name, FIELD_CLASSIFIER_PROMPT, pattern, max_tokens)
    cleaned = text.strip().lower()
    for field in VALID_FIELDS:
        if cleaned.startswith(field):
            return field
    return "coder"


def _call_with_system_prompt(url, model_name, system_prompt, user_content, max_tokens):
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
        with urllib.request.urlopen(req, timeout=60) as resp:
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
        raise RuntimeError("field-classifier call failed: %s" % e)
    return "".join(chunks)


def propose_and_apply_pattern_fix(field, pattern_reason, config_path=sm3.CONFIG_PATH):
    """Feeds a detected pattern into milestone 3's existing verified pipeline, unchanged. Returns
    a dict: {applied: bool, verdict, proposed_text, backup_path (if applied)}. Never applies an
    edit that the independent verifier didn't PASS -- same safety bar as every other config edit
    in this project."""
    current = sm3.load_config().get(field, "")
    proposed = sm3.propose_config_edit(field, current, pattern_reason)
    verdict = sm3.verify_config_edit(field, current, proposed)
    if not sm3.is_edit_verified(verdict):
        return {"applied": False, "verdict": verdict, "proposed_text": proposed}
    new_config, backup_path = sm3.apply_edit_with_backup(field, proposed, config_path=config_path)
    return {"applied": True, "verdict": verdict, "proposed_text": proposed, "backup_path": backup_path}


def run_meta_review(lessons_path, field=None, config_path=sm3.CONFIG_PATH, n=20):
    """The real entry point: load recent lessons, look for a genuine recurring pattern, and
    only if one is found, run it through the verified propose/verify/apply pipeline. `field`
    defaults to None, meaning "figure out which role this pattern is actually about" via
    classify_pattern_field() rather than always assuming coder -- pass an explicit field to
    force a specific target instead. Returns {"pattern_found": False} if nothing recurring was
    detected -- NONE is a correct, expected outcome much of the time, not a failure."""
    lessons = load_recent_lessons(lessons_path, n)
    pattern = find_recurring_pattern(lessons)
    if pattern is None:
        return {"pattern_found": False}
    target_field = field if field is not None else classify_pattern_field(pattern)
    result = propose_and_apply_pattern_fix(target_field, pattern, config_path=config_path)
    result["pattern_found"] = True
    result["pattern"] = pattern
    result["field"] = target_field
    return result
