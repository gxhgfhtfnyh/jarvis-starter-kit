# -*- coding: utf-8 -*-
"""The local-model (Pip's backend, local-qwen on port 8090) quick-chat reply leg for the voice
pipeline (2026-11-13, his overnight ask: "it should link to your session and responses max is
2-3 seconds... work with pip to perfect your talking"). Real measured finding that makes this
possible at all: the model is launched with --reasoning-budget 8000 (a genuine "thinking" model),
and by default burns the ENTIRE max_tokens budget on reasoning_content before any spoken content
appears (confirmed live: 300 max_tokens, 24s, zero content tokens). The documented llama.cpp/Qwen3
fix is `chat_template_kwargs: {"enable_thinking": false}` in the request body -- NOT the classic
`/no_think` in-text tag, which this server's chat template does not honor (tested, still thought).
With thinking disabled: first content token ~1.1s, a full short reply ~2.8s total -- genuinely
inside the 2-3s target for the voice fast path. This is the SAME server Pip (the Pi CLI harness)
already talks to via ~/.pi/agent/models.json's local-qwen provider; this module just calls its
OpenAI-compatible HTTP endpoint directly (no `pi` CLI subprocess spin-up cost) for the voice path
specifically, while Pip's own CLI identity stays for coding tasks.

Usage, same reply_fn(heard_text) -> generator-of-text-chunks contract as voice_loop.get_reply_cli(),
so it's a drop-in alternative reply_fn for run_loop_live_handoff():
    import local_reply
    handoff = run_loop_live_handoff(heard, out_dir, reply_fn=local_reply.get_reply_local)
"""
import json
import urllib.error
import urllib.request

LOCAL_QWEN_URL = "http://127.0.0.1:8090/v1/chat/completions"

VOICE_PERSONA = (
    "You are Jarvis, the owner's personal assistant and friend, speaking out loud to him right "
    "now (this gets read aloud by TTS, not read as text). Talk exactly like you text him: a "
    "chill friend, not a report -- plain casual sentences, no cat talk, no bullet points or "
    "lists (you can't speak a bulleted list naturally), no headers. Competent and direct "
    "underneath, real reactions allowed. Keep it short -- a couple sentences, not a paragraph, "
    "since every word has to actually be spoken before he hears the rest."
)


def build_payload(heard_text, system_prompt=VOICE_PERSONA, max_tokens=200, model="qwen"):
    """Pure. The real request body, with chat_template_kwargs.enable_thinking=false -- the one
    thing that actually skips the reasoning_content stage (verified live; the in-text `/no_think`
    tag does not work on this server/model combination)."""
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": heard_text},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }


def parse_sse_content_delta(line):
    """One raw SSE line from the streamed response -> the content text it carries, or None if
    this line carries no displayable content (a role-only delta, a reasoning_content delta --
    deliberately dropped here since thinking is disabled and none should arrive, but dropped
    defensively rather than assumed-absent --, or a non-JSON/keepalive line). Pure."""
    line = line.strip()
    if not line.startswith("data: "):
        return None
    data = line[6:]
    if data == "[DONE]":
        return None
    try:
        d = json.loads(data)
    except (ValueError, TypeError):
        return None
    choices = d.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    return delta.get("content") or None


def get_reply_local(heard_text, url=LOCAL_QWEN_URL, system_prompt=VOICE_PERSONA, timeout=30):
    """The real quick-chat leg, local-qwen (Pip's backend model) instead of a Claude CLI call --
    no subprocess, no network round-trip to Anthropic, no Claude usage cost, and (with thinking
    disabled) genuinely sub-3s for a short reply. Same reply_fn(heard_text) -> generator-of-chunks
    contract as voice_loop.get_reply_cli(), so it's a drop-in replacement in
    run_loop_live_handoff()'s reply_fn slot."""
    payload = json.dumps(build_payload(heard_text, system_prompt=system_prompt)).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw_line in resp:
                text = parse_sse_content_delta(raw_line.decode("utf-8", errors="ignore"))
                if text:
                    yield text
    except urllib.error.URLError as e:
        raise RuntimeError("local-qwen call failed (is moe_relaunch.py's server up on 8090?): %s" % e)
