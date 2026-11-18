"""
Decision helper that asks an AI chat endpoint a strict question and returns a clean answer.
"""

import json
import re
import urllib.request
import urllib.error
from pathlib import Path
from typing import Callable, Optional, Any


def ask_openai_compatible(prompt: str) -> str:
    """
    Default ask function that POSTs to a local OpenAI-compatible endpoint.
    """
    key_path = Path(r"C:\LocalAI\omniroute_trial\claude_key.txt")
    if not key_path.exists():
        raise FileNotFoundError(f"API key file not found: {key_path}")

    bearer_token = key_path.read_text(encoding="utf-8").strip()

    url = "http://127.0.0.1:20128/v1/chat/completions"
    data = {
        "model": "free-fast",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 120,
        "temperature": 0.0,
    }

    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer_token}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
            return result["choices"][0]["message"]["content"]
    except urllib.error.URLError as e:
        raise RuntimeError(f"Network error: {e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Invalid response format: {e}")


def _extract_json(text: str) -> Optional[dict]:
    """
    Find the first {...} JSON object in the text, even if wrapped in code fences or chatter.
    """
    # Try to find JSON in code fences first
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1))
        except json.JSONDecodeError:
            pass

    # Find first {...} pattern
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            return json.loads(brace_match.group(0))
        except json.JSONDecodeError:
            pass

    return None


def _validate_answer(kind: str, answer: Any, options: Optional[list] = None) -> tuple[Any, Optional[str]]:
    """
    Validate answer matches the expected kind. Returns (validated_answer, error_message).
    """
    if kind == "yesno":
        if isinstance(answer, bool):
            return answer, None
        if isinstance(answer, str):
            lower = answer.lower()
            if lower in ("true", "yes", "1"):
                return True, None
            if lower in ("false", "no", "0"):
                return False, None
        return None, f"yesno answer must be boolean, got {type(answer).__name__}"

    elif kind == "choice":
        if options is None:
            return None, "choice kind requires options list"
        if isinstance(answer, str) and answer in options:
            return answer, None
        return None, f"choice answer must be one of {options}, got {answer!r}"

    elif kind == "score":
        if isinstance(answer, (int, float)):
            val = int(answer)
            if 1 <= val <= 5:
                return val, None
        return None, f"score answer must be int 1..5, got {answer!r}"

    return None, f"unknown kind: {kind}"


def _clamp_confidence(confidence: Any) -> float:
    """
    Clamp confidence to 0..1 range.
    """
    try:
        val = float(confidence)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, val))


def decide(
    state: str,
    question: str,
    kind: str = "yesno",
    options: Optional[list] = None,
    ask: Optional[Callable[[str], str]] = None,
) -> dict:
    """
    Ask an AI a strict question and return a structured answer.

    Args:
        state: Context/state to provide to the model
        question: The question to ask
        kind: 'yesno' (bool), 'choice' (str from options), or 'score' (int 1-5)
        options: Required for 'choice' kind - list of allowed answer strings
        ask: Function(prompt) -> reply_text. Defaults to ask_openai_compatible.

    Returns:
        dict with keys: answer, confidence, error (if any)
    """
    if ask is None:
        ask = ask_openai_compatible

    # Build the prompt
    if kind == "choice":
        if options is None:
            return {"answer": None, "confidence": 0.0, "error": "choice kind requires options list"}
        kind_instruction = f'Answer with exactly one of: {", ".join(repr(o) for o in options)}.'
    elif kind == "yesno":
        kind_instruction = 'Answer with true or false.'
    elif kind == "score":
        kind_instruction = 'Answer with an integer from 1 to 5.'
    else:
        return {"answer": None, "confidence": 0.0, "error": f"unknown kind: {kind}"}

    prompt = f"""You are a decision engine. Reply with ONLY one line of JSON, no extra words, no markdown, no explanation.

State: {state}
Question: {question}
{kind_instruction}

Reply format: {{"answer": <value>, "confidence": <0 to 1>}}"""

    try:
        reply = ask(prompt)
    except Exception as e:
        return {"answer": None, "confidence": 0.0, "error": f"ask failed: {e}"}

    if not reply or not reply.strip():
        return {"answer": None, "confidence": 0.0, "error": "empty reply"}

    parsed = _extract_json(reply)
    if parsed is None:
        return {"answer": None, "confidence": 0.0, "error": "no valid JSON found in reply"}

    answer = parsed.get("answer")
    confidence = parsed.get("confidence", 0.0)

    validated_answer, error = _validate_answer(kind, answer, options)
    if error:
        return {"answer": None, "confidence": 0.0, "error": error}

    return {
        "answer": validated_answer,
        "confidence": _clamp_confidence(confidence),
        "error": None,
    }