# -*- coding: utf-8 -*-
"""N-candidates + scored arbitration for Kyogen (Nov 13 2026 cognition research, Global
Workspace Theory thread): the real, buildable gap GWT research surfaced -- Kyogen generates
exactly one candidate per attempt and gates it pass/fail. The concrete upgrade: generate 2-3
candidate approaches, score each on a scale rather than binary PASS/FAIL, and arbitrate to the
best-scored one -- closer to the Generator->Critic->Meta pattern found in the "Theater of Mind"
GWT-for-LLMs paper (arXiv 2604.08206), while being honest that paper itself has zero
experiments; this module's own live testing is the real evidence for whether it helps.
"""
import re

import swarm_milestone1 as sm

SCORING_PROMPT = (
    "You are scoring one candidate solution to a coding task on a 0-100 scale for how likely it "
    "is to be fully correct. Your entire reply must be one line: a number 0-100 followed by a "
    "colon and a one-sentence reason, e.g. '72: handles most cases but eviction order looks "
    "off for capacity 1.' Be genuinely discriminating -- candidates should rarely tie exactly."
)


def parse_score(text):
    """Pure. Same contract as swarm_milestone1.parse_confidence_score -- extracts a leading
    0-100 score, or None if unparseable (treated as the lowest priority by the caller, never
    silently picked as best)."""
    m = re.match(r"\s*(\d{1,3})\s*:", text)
    if not m:
        return None
    score = int(m.group(1))
    return score if 0 <= score <= 100 else None


def generate_candidates(coder_prompt, n=3):
    """Calls the coder role n times independently on the same prompt. Returns a list of
    extracted code strings (entries may be None if a given attempt produced no fenced code)."""
    candidates = []
    for _ in range(n):
        text = sm._call_role("coder", coder_prompt)
        candidates.append(sm.extract_code(text))
    return candidates


def score_candidate(task, analysis, code, max_tokens=80):
    """Scores one candidate. Returns (score_int_or_None, raw_verdict_text)."""
    prompt = "TASK:\n%s\n\nANALYSIS:\n%s\n\nCANDIDATE CODE:\n%s" % (task, analysis, code or "(none)")
    text = _call_scoring(prompt, max_tokens)
    return parse_score(text), text


def _call_scoring(prompt, max_tokens=80):
    """Direct call using SCORING_PROMPT rather than any of swarm_milestone1's existing
    ROLE_PROMPTS entries -- scoring is its own role, not a reuse of the binary verifier."""
    import json
    import urllib.error
    import urllib.request

    url, model_name = sm.ROLE_MODEL_URL.get("verifier", (sm.LOCAL_QWEN_URL, "qwen"))
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": SCORING_PROMPT},
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
        raise RuntimeError("arbitration scoring call failed: %s" % e)
    return "".join(chunks)


def pick_best(task, analysis, candidates):
    """Scores every candidate, picks the highest-scored one. An unparseable score is treated as
    -1 (lowest possible priority), never silently promoted to 'best' by a parsing failure.
    Returns (best_code, best_score, all_scored: list of (code, score, raw_text))."""
    scored = []
    for code in candidates:
        score, raw = score_candidate(task, analysis, code)
        scored.append((code, score if score is not None else -1, raw))
    best_code, best_score, _ = max(scored, key=lambda t: t[1])
    return best_code, best_score, scored
