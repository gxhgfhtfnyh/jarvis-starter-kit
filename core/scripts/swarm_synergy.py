# -*- coding: utf-8 -*-
"""Synergy of Thoughts routing for Kyogen (Nov 13 2026 cognition research, dual-process/System
1-2 thread): arXiv 2402.02563 measured 38.3-75.1% API cost reduction and up to 69.1% token
reduction by having small/fast models answer first and only escalating to a large/slow model
when the small models genuinely disagree. Applied here to the verifier role specifically: two
or more small swap-pool models independently verify a candidate using the SAME verifier prompt
as the main pipeline, and the big (always-on) model only gets called as a tie-breaker when they
disagree -- the faithful version of Kyogen's existing "small models for narrow tasks" instinct,
not just a surface resemblance to it.
"""
import json
import urllib.error
import urllib.request

import swarm_milestone1 as sm


CHECKLIST_VERIFIER_PROMPT = (
    "You are a strict code reviewer. You will be given a task, a list of specific edge cases "
    "to check, and candidate code. Real bug found live 2026-11-13: two independent small-model "
    "verifiers both gave a vague overall PASS and missed a real bug (n=1 treated as prime) that "
    "was LISTED in the analysis they were given -- a holistic impression let a known edge case "
    "slip through. Do not do that. Go through the edge cases ONE BY ONE in your own head and "
    "confirm the code actually handles each one correctly before giving a verdict. Your reply "
    "must be ONE line: PASS only if the code genuinely handles every listed edge case, or "
    "FAIL: <which specific edge case breaks it>."
)


def _call_at(url, model_name, prompt, max_tokens=100, timeout=60, system_prompt=CHECKLIST_VERIFIER_PROMPT):
    """Blocking call using the checklist-style verifier system prompt (the real fix for the
    shared-blind-spot bug found live), against an EXPLICIT (url, model_name) pair rather than a
    ROLE_MODEL_URL lookup -- keeps this module self-contained and directly testable without
    mutating global routing state."""
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
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
                text = delta.get("content")
                if text:
                    chunks.append(text)
    except urllib.error.URLError as e:
        raise RuntimeError("synergy call to %s (%s) failed: %s" % (url, model_name, e))
    return "".join(chunks)


def _verdict_is_pass(verdict):
    """Pure. True if a verifier verdict reads as PASS (same convention as run_condition_c's own
    'FAIL' in verdict.upper() check, inverted and made explicit for comparison purposes)."""
    return "FAIL" not in verdict.upper()


def check_agreement(verdicts):
    """Pure. True if all given verdicts land on the same PASS/FAIL side -- genuine agreement,
    not textual identity (two models can both say PASS with different wording and still
    agree). Returns False (disagreement) for an empty list, since there's nothing to agree on."""
    if not verdicts:
        return False
    sides = {_verdict_is_pass(v) for v in verdicts}
    return len(sides) == 1


def synergy_verify(task, analysis, code, small_model_endpoints, escalate_role="verifier", max_tokens=100):
    """small_model_endpoints: a list of (url, model_name) tuples for the small models to try
    first. Runs the standing verifier prompt against each. If they agree, returns that verdict
    with NO big-model call -- the real cost saving. If they disagree, escalates to
    escalate_role (normally the big always-on model, via sm._call_role) as the tie-breaker.
    Returns (final_verdict, escalated: bool, small_verdicts: list)."""
    prompt = "TASK:\n%s\n\nANALYSIS:\n%s\n\nCANDIDATE CODE:\n%s" % (task, analysis, code or "(none)")
    small_verdicts = [_call_at(url, model_name, prompt, max_tokens=max_tokens) for url, model_name in small_model_endpoints]
    if check_agreement(small_verdicts):
        return small_verdicts[0], False, small_verdicts
    final_verdict = sm._call_role(escalate_role, prompt, max_tokens=max_tokens)
    return final_verdict, True, small_verdicts
