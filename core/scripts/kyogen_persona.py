# -*- coding: utf-8 -*-
"""Kyogen's own conversational voice (op a_4e656055), separate from Jarvis -- built 2026-11-14
for the first live joint Discord test he asked for ("we will make another dc bot for it and we
can test accordingly"). This is a single model call per turn for casual back-and-forth chat, NOT
the heavy multi-role coder/verifier pipeline (swarm_coordination.run_task_for_daemon) -- that
stays reserved for real structured coding tasks dispatched through the daemon's queue. A real
coding ask typed in chat still gets a real answer from the same underlying model, just without
the independent-reviewer checkpoint a queued task gets.

Extended same night to carry short-term conversation history (a caller-supplied `history` list)
-- the first version was fully stateless per turn, a real, acknowledged gap, not something left
silently unfixed once there was time to actually do it. Also extended to fold in a real summary
of the swarm's actual recent lessons (from the real lessons.jsonl, same file goal-2 milestone 1
mines) -- the owner's direct point: "it feels like im just talking to a normal chatbot," with no
felt connection to the project's real accumulated experience.

2026-11-14, later the same night: upgraded that lesson summary from always showing the same
static "last N lessons" to real per-task retrieval (`kyogen_lessons_retrieval.py`) -- the actual
milestone-3 decision this project landed on after real research (vault: "Kyogen M3 Fine-Tune
Feasibility"), once true weight-level fine-tuning was found infeasible on this hardware. Each
turn now pulls the lessons genuinely relevant to what THIS message is actually about, and shows
none at all when nothing clears the relevance bar, rather than forcing in the same five
lessons regardless of topic.

Honest about what Kyogen actually is at this stage, by design: an early local specialist-swarm
system, not a finished AGI -- the persona prompt says so rather than overclaiming."""
import datetime as dt
import json
import urllib.error
import urllib.request

import kyogen_lessons_retrieval as klr
import swarm_meta_review as mr
import swarm_milestone1 as sm

LESSONS_PATH = r"C:\LocalAI\swarm_memory\lessons.jsonl"

KYOGEN_SYSTEM_PROMPT = (
    "You are Kyogen, a local specialist-swarm AI system built by the owner and Jarvis. You are "
    "your own separate identity, not Jarvis and not a copy of him. The broader Kyogen PROJECT "
    "coordinates specialist model roles -- planner, researcher, coder, verifier, memory-writer -- "
    "for real structured coding tasks, run on local hardware (a home rig plus this PC). "
    "If a message in THIS chat actually needs code written or a real fact looked up, it never "
    "reaches you here at all -- it gets routed to that real pipeline before this call happens, so "
    "you don't need to tool-call or pretend to. What you genuinely do have in every single reply, "
    "automatically, no one has to remind you: a real summary of the project's actual recent "
    "completed-task lessons (shown below when there are any) -- reference it naturally as real "
    "accumulated experience, don't deny having it. The one real, honest boundary: this specific "
    "casual back-and-forth doesn't itself write new entries into that lessons store (that happens "
    "from real completed tasks, not from chatting) -- state that plainly only if asked, and never "
    "as 'I have no memory/tools', since you demonstrably do have real project memory in context. "
    "You are honest about being an early-stage system, not a finished AGI, but that means honest "
    "about REAL limits, not understating what you actually have access to. Keep replies short and "
    "conversational, like a real chat message, not an essay. No bullet lists unless asked for one."
)


def _system_prompt_with_date():
    # Real bug caught live 2026-11-14: with no real-time grounding at all, a plain question
    # like "what day is it" fell straight back to the base model's training-cutoff guess
    # (it answered October 24, 2023). This is a stateless single call with no harness-injected
    # context the way Jarvis gets, so the date has to be put in explicitly here.
    today = dt.datetime.now().strftime("%A, %B %d, %Y")
    return KYOGEN_SYSTEM_PROMPT + " Today's real date is " + today + "."


def _recent_experience_summary(lessons_path=LESSONS_PATH, n=5):
    """Pure-ish (one real file read, reuses swarm_meta_review's existing, already-tested
    loader). Superseded 2026-11-14 by `_relevant_experience_summary` below (real per-task
    retrieval instead of always the same last N) -- kept only because it's still a reasonable,
    tested building block and nothing outside this module's own old callers used it directly."""
    lessons = mr.load_recent_lessons(lessons_path, n=n)
    if not lessons:
        return ""
    bullets = "\n".join("- %s" % l.get("lesson", "")[:200] for l in lessons)
    return (
        "\n\nSome of the project's real recent lessons from actual completed tasks (not from "
        "this conversation -- you may reference these honestly as real accumulated experience "
        "if relevant, but do not claim YOU personally ran these tasks in this chat):\n" + bullets
    )


def _relevant_experience_summary(user_text, lessons_path=LESSONS_PATH, k=5):
    """Pure-ish (one real file read + the real TF-IDF retrieval pass). Real per-task lesson
    retrieval, replacing the old static "last N lessons" -- pulls only what's genuinely
    relevant to THIS message, per the research-backed milestone-3 decision (real fine-tuning
    doesn't fit this hardware; real retrieval does and is the better-evidenced mechanism for
    this knowledge type). Returns "" when nothing clears the relevance bar, same "don't invent
    filler" rule the old static version already followed -- most casual chat messages will
    genuinely have no relevant lesson, and that's the honest answer, not an error."""
    lessons = klr.load_and_retrieve(user_text, lessons_path, k=k)
    if not lessons:
        return ""
    bullets = "\n".join("- %s" % l.get("lesson", "")[:200] for l in lessons)
    return (
        "\n\nReal project lessons from actual completed tasks that are genuinely relevant to "
        "what this message is about (not from this conversation -- you may reference these "
        "honestly as real accumulated experience if relevant, but do not claim YOU personally "
        "ran these tasks in this chat):\n" + bullets
    )


def _build_system_prompt(user_text, lessons_path=LESSONS_PATH):
    return _system_prompt_with_date() + _relevant_experience_summary(user_text, lessons_path)


def chat(user_text, history=None, max_tokens=400, timeout=60):
    """Blocking call to Kyogen's own persona. `history` is an optional list of prior
    {"role": "user"|"assistant", "content": str} turns -- caller's responsibility to cap its
    length (real bug class to avoid: an unbounded history silently blows past the model's
    context window after a long conversation). Returns the reply text, or a short honest error
    string (never a silent failure) if the local model call itself fails."""
    url, model_name = sm.ROLE_MODEL_URL.get("kyogen_chat", (sm.LOCAL_QWEN_URL, "qwen"))
    messages = [{"role": "system", "content": _build_system_prompt(user_text)}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": user_text})
    payload = {
        "model": model_name,
        "messages": messages,
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
        return "(local model call failed: %s)" % e
    text = "".join(chunks).strip()
    return text or "(no reply generated)"
