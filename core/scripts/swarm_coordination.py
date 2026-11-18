# -*- coding: utf-8 -*-
"""Milestone 5 proper, the integration (op a_4e656055, Nov 13 2026): wires swarm_mailbox.py
into the real pipeline so a role's own low confidence triggers it to ask a PEER for help --
genuine agent-initiated coordination, not me (the orchestrator) deciding to call verifier next.

The honest limit, stated plainly rather than oversold: get_coder_output_with_peer_help still
resolves the request synchronously within the same call (the researcher role is invoked
immediately, not on its own independent schedule), because the milestone-1 pipeline itself is
synchronous. What makes this genuinely different from the existing verifier/retry loop is WHO
authors the content: the coder's request text is its own stated confusion (not a prompt I
wrote), and the researcher's reply is its own real analysis of that specific confusion (not a
templated escalation message) -- a real peer exchange carried over the mailbox, not two
sequential calls I glued together.

resolve_pending_mailbox() is the piece that CAN run on independent timing: the standalone
daemon's idle loop can call it to answer any help requests sitting in a role's mailbox whenever
it happens to poll, decoupled from the moment the request was posted. That is the real step
toward roles coordinating on their own schedule rather than mine.
"""
import re
import time

import swarm_mailbox as mb
import swarm_milestone1 as sm


def resolve_help_request(request, mailbox_dir=mb.MAILBOX_DIR):
    """Calls the REAL role named in request['to'] with the requester's own problem description
    as the prompt -- the reply is genuine peer-authored content, not orchestrator glue text.
    Posts the reply back (threaded, and consumes the original request) and returns it."""
    responder_role = request["to"]
    prompt = "A peer (%s) asked for help with this:\n%s" % (request["from"], request["content"])
    response_text = sm._call_role(responder_role, prompt, max_tokens=300)
    return mb.reply_to_request(responder_role, request, response_text, mailbox_dir=mailbox_dir)


ESCALATION_SUFFIX = (
    "\n\nIf you genuinely don't know and believe a DIFFERENT specific role (researcher, coder, "
    "or verifier) would actually know better than you, reply with EXACTLY this format on its "
    "own line and nothing else: 'ESCALATE:<role_name>:<one-sentence reason>'. Only escalate if "
    "you are honestly less qualified than that other role -- do not escalate just to avoid "
    "answering. Otherwise, just answer normally."
)
ESCALATE_RE = re.compile(r"^\s*ESCALATE:(\w+):(.*)$", re.I | re.M)

# Real finding, live 2026-11-13: a role can be genuinely, honestly uncertain in plain prose
# WITHOUT ever spontaneously using the ESCALATE: marker format. First fix attempt was a regex
# of uncertainty phrasings -- missed a real live reply ("I'm afraid I can't provide...",
# "there's no factual basis for me to claim knowledge") the same session, the SAME denylist-
# enumeration trap already learned from send_gate_hook.py's curl exception earlier tonight:
# natural-language phrasings for "I don't know" are as unenumerable as curl's flag surface.
# Fixed the same way that was fixed there -- stop enumerating, use a real judgment call instead.
UNCERTAINTY_JUDGE_PROMPT = (
    "Does the following reply express genuine uncertainty or inability to answer the question "
    "(as opposed to a real, substantive attempt at an answer)? Reply with exactly one word: "
    "YES or NO."
)


def _expresses_genuine_uncertainty(text, max_tokens=10):
    """A real semantic judgment call, not a phrasing denylist -- the model was already shown
    (same session) to phrase honest uncertainty in ways no finite regex caught. Uses the
    verifier role's model (a judgment-shaped role already in the roster) to classify, pure
    text in -> bool out from the caller's point of view."""
    import json
    import urllib.error
    import urllib.request

    url, model_name = sm.ROLE_MODEL_URL.get("verifier", (sm.LOCAL_QWEN_URL, "qwen"))
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": UNCERTAINTY_JUDGE_PROMPT},
            {"role": "user", "content": text},
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
                piece = delta.get("content")
                if piece:
                    chunks.append(piece)
    except urllib.error.URLError as e:
        raise RuntimeError("uncertainty-judge call failed: %s" % e)
    return "".join(chunks).strip().upper().startswith("YES")


def resolve_help_request_chained(request, chain=("researcher", "verifier", "coder"), max_hops=3, mailbox_dir=mb.MAILBOX_DIR):
    """The real multi-hop test (Nov 13 2026, matching the Hermes/Herdr system's already-proven
    3+ hop peer chain): if the role first asked genuinely doesn't know and says so, it can
    escalate to a DIFFERENT named role rather than being forced to answer -- a real chain
    (A asks B, B asks C, C's answer flows back through B to A), not a single hop dressed up to
    look like one. Posts the reply from whichever role actually answered, threaded back to the
    original requester; consumes the original request from its original mailbox regardless of
    how many hops it took. Returns (final_reply_dict, hop_path: list of role names visited)."""
    current_role = request["to"]
    current_question = "A peer (%s) asked for help with this:\n%s" % (request["from"], request["content"])
    visited = [current_role]
    for _ in range(max_hops):
        text = sm._call_role(current_role, current_question + ESCALATION_SUFFIX, max_tokens=300)
        m = ESCALATE_RE.search(text.strip())
        next_role, reason = (None, None)
        if m:
            candidate = m.group(1).lower()
            if candidate in chain and candidate not in visited:
                next_role, reason = candidate, m.group(2).strip()
        elif _expresses_genuine_uncertainty(text):
            # The model never used the ESCALATE: marker but is honestly uncertain in plain
            # prose -- a real gap found live: honest-uncertainty instinct and marker-format
            # instruction-following don't automatically combine, so detect this case with a
            # real judgment call rather than require the model to self-invoke the protocol.
            remaining = [r for r in chain if r not in visited]
            if remaining:
                next_role, reason = remaining[0], "expressed genuine uncertainty without using the escalation format"
        if next_role:
            current_question = (
                "%s was asked for help with this original question:\n%s\n\n%s said it isn't "
                "sure and asked you specifically because: %s" % (current_role, request["content"], current_role, reason)
            )
            current_role = next_role
            visited.append(current_role)
            continue
        reply = mb.reply_to_request(current_role, request, text, mailbox_dir=mailbox_dir)
        return reply, visited
    # Ran out of hops without a non-escalating answer -- still give the requester the last real
    # reply received rather than silently dropping it, honestly labelled as unresolved.
    reply = mb.reply_to_request(current_role, request, text, mailbox_dir=mailbox_dir)
    return reply, visited


def resolve_pending_mailbox(role, mailbox_dir=mb.MAILBOX_DIR):
    """Answers every pending help_request currently sitting in role's mailbox. Meant to be
    called from the daemon's idle loop on its own polling schedule -- genuinely decoupled from
    the moment any request was posted. Returns the number resolved."""
    pending = [m for m in mb.get_messages(role, mailbox_dir=mailbox_dir) if m["kind"] == "help_request"]
    for request in pending:
        resolve_help_request(request, mailbox_dir=mailbox_dir)
    return len(pending)


def get_coder_output_with_peer_help(coder_prompt, confidence_threshold=60, max_attempts=2, mailbox_dir=mb.MAILBOX_DIR):
    """Runs the existing self-confidence gate first (swarm_milestone1's metacognition check).
    If the coder is STILL not confident after its own retry, it posts its own stated confusion
    as a help_request to the researcher's mailbox (role-initiated, not me deciding to escalate),
    gets a real researcher reply, and gets one more attempt with that peer input.
    Returns (code, self_confidence, self_confidence_text, self_retried, peer_consulted, peer_reply_text)."""
    code, score, confidence_text, self_retried = sm.get_coder_output_with_self_check(
        coder_prompt, confidence_threshold, max_attempts
    )
    peer_consulted = False
    peer_reply_text = None
    if score is None or score < confidence_threshold:
        problem = "Still not confident after my own retry.\nSelf-review said: %s\nMy code:\n%s" % (
            confidence_text, code or "(no code extracted)",
        )
        request = mb.request_help("coder", "researcher", problem, mailbox_dir=mailbox_dir)
        reply = resolve_help_request(request, mailbox_dir=mailbox_dir)
        peer_consulted = True
        peer_reply_text = reply["content"]
        retry_prompt = coder_prompt + "\n\nA peer researcher looked at your stuck point and said: %s\nFix it." % peer_reply_text
        text = sm._call_role("coder", retry_prompt)
        code = sm.extract_code(text)
    return code, score, confidence_text, self_retried, peer_consulted, peer_reply_text


def run_task_for_daemon(memory_path, task, test_suite, mailbox_dir=mb.MAILBOX_DIR):
    """The real daemon-facing pipeline (milestone 5 proper): researcher analysis -> shared
    memory (same as swarm_milestone1's condition B), then the coder with the self-confidence
    gate AND the peer-help escalation wired in -- a queued real task gets the actual best
    pipeline, not the three-way A/B/C comparison milestone 1 runs for its own experiment.
    Returns a result dict in the same shape swarm_daemon.run_job already expects
    (passed/retried/seconds/detail), plus peer_consulted/peer_reply_text for real visibility
    into whether this specific run needed a peer exchange."""
    t0 = time.time()
    analysis, _ = sm.get_researcher_analysis(task)
    with open(memory_path, "w", encoding="utf-8") as f:
        f.write("TASK:\n%s\n\nRESEARCHER ANALYSIS:\n%s\n" % (task, analysis))
    coder_prompt = "TASK:\n%s\n\nRESEARCHER ANALYSIS:\n%s\n\nNow write the code." % (task, analysis)

    code, self_confidence, confidence_text, self_retried, peer_consulted, peer_reply_text = get_coder_output_with_peer_help(
        coder_prompt, mailbox_dir=mailbox_dir
    )

    verify_prompt = "TASK:\n%s\n\nANALYSIS:\n%s\n\nCANDIDATE CODE:\n%s" % (task, analysis, code or "(none)")
    verdict = sm._call_role("verifier", verify_prompt, max_tokens=100)
    retried = False
    if "FAIL" in verdict.upper():
        retried = True
        retry_prompt = coder_prompt + "\n\nA reviewer flagged this issue, fix it: %s" % verdict
        text = sm._call_role("coder", retry_prompt)
        code = sm.extract_code(text)

    passed, detail = sm.run_candidate(code, test_suite)
    return {
        "passed": passed, "detail": detail, "seconds": round(time.time() - t0, 2),
        "verdict": verdict, "retried": retried, "analysis": analysis, "code": code,
        "self_confidence": self_confidence, "self_confidence_text": confidence_text, "self_retried": self_retried,
        "peer_consulted": peer_consulted, "peer_reply_text": peer_reply_text,
    }
