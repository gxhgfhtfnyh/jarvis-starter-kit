# -*- coding: utf-8 -*-
"""Real, genuinely different integrity layer (op a_4e656055, Nov 14 2026), built after he said
"if u can finish it then do it" about a real live finding: a self-generated test asserted a
factually WRONG expected value, and neither the ast.parse() syntax check nor the semantic
reviewer caught it -- "is this syntactically valid" and "does this test the right concept" are
both different questions from "is this specific number actually correct."

The mechanism here is deliberately NOT another LLM judgment call layered on top (that would be
the same failure mode risk as the semantic reviewer itself being wrong). It is a real, mechanical
cross-check: ask a SEPARATE model call to write an independent reference implementation, then
actually EXECUTE both the test's asserted call expressions against that reference and compare
the real output to the asserted expected value. Two independently-derived answers agreeing is a
real signal; disagreeing is a real, concrete red flag -- this does not prove either is correct
(the reference could also be wrong), but it is a genuinely different kind of check than more
semantic review, and it is honest about that limit rather than oversold.
"""
import ast
import json
import os
import re
import subprocess
import tempfile

import swarm_milestone1 as sm

REFERENCE_PROMPT = (
    "You are writing a REFERENCE implementation for a coding task -- correctness matters far "
    "more than efficiency; a slow, obviously-correct brute-force approach is preferred over a "
    "clever one that might have a subtle bug. Return ONLY the Python code in one ```python "
    "fenced block, no explanation."
)

# Matches the common, simple pattern: assert <call expression> == <literal value>[, optional msg]
# Deliberately conservative -- only extracts assertions this simple shape can parse with
# confidence; anything more complex (loops, variables, multi-line) is left unchecked rather than
# mis-parsed.
ASSERT_EQ_RE = re.compile(r"^\s*assert\s+(.+?)\s*==\s*([^,#\n]+?)\s*(?:,.*)?$", re.M)


def generate_reference_implementation(task_description, max_tokens=800):
    """Calls a real, independent model completion for a reference implementation -- a SEPARATE
    call from whatever generated the original test suite, same discipline as every other
    independent-verification step in this project. Returns the extracted code, or None if
    nothing parseable came back."""
    url, model_name = sm.ROLE_MODEL_URL.get("coder", (sm.LOCAL_QWEN_URL, "qwen"))
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": REFERENCE_PROMPT},
            {"role": "user", "content": task_description},
        ],
        "stream": True,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    text = _call(url, payload)
    return sm.extract_code(text)


def _call(url, payload):
    import urllib.error
    import urllib.request

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
                piece = delta.get("content")
                if piece:
                    chunks.append(piece)
    except urllib.error.URLError as e:
        raise RuntimeError("reference-implementation call failed: %s" % e)
    return "".join(chunks)


def extract_simple_assertions(test_suite):
    """Pure. Returns a list of (call_expr, expected_literal) tuples for assertions matching the
    simple `assert <call> == <literal>` shape. Anything it can't confidently parse is simply not
    included -- this never guesses at a more complex assertion's meaning."""
    out = []
    for m in ASSERT_EQ_RE.finditer(test_suite):
        call_expr, expected_literal = m.group(1).strip(), m.group(2).strip()
        try:
            # literal_eval, not parse(mode="eval") -- parse alone accepts a bare Name node
            # (e.g. a variable reference like `expected_value`), which is not a literal and
            # can't be safely re-embedded as the RHS of a generated comparison below.
            ast.literal_eval(expected_literal)
        except (ValueError, SyntaxError, TypeError):
            continue
        out.append((call_expr, expected_literal))
    return out


def check_against_reference(reference_code, assertions, timeout=15):
    """Runs the real reference implementation (sandboxed subprocess, same pattern as
    run_candidate) against each extracted call expression and compares to the asserted literal.
    Returns a list of mismatch dicts: {call, expected, actual} -- empty if everything agreed or
    nothing could be checked. A call that errors against the reference is reported as a mismatch
    too (actual='<error>: ...'), since the test asserting a clean value for an input the
    reference can't even evaluate is itself a real red flag."""
    if not assertions:
        return []
    with tempfile.TemporaryDirectory() as d:
        ref_path = os.path.join(d, "reference.py")
        with open(ref_path, "w", encoding="utf-8") as f:
            f.write(reference_code)
        checker_lines = ["import sys", "sys.path.insert(0, r'%s')" % d, "from reference import *", "import json", "results = []"]
        for call_expr, expected_literal in assertions:
            checker_lines.append(
                "try:\n"
                "    _actual = (%s)\n"
                "    results.append({'call': %r, 'expected': %r, 'actual': repr(_actual), 'matches': _actual == (%s)})\n"
                "except Exception as _e:\n"
                "    results.append({'call': %r, 'expected': %r, 'actual': '<error>: %%s' %% _e, 'matches': False})\n"
                % (call_expr, call_expr, expected_literal, expected_literal, call_expr, expected_literal)
            )
        checker_lines.append("print(json.dumps(results))")
        checker_path = os.path.join(d, "checker.py")
        with open(checker_path, "w", encoding="utf-8") as f:
            f.write("\n".join(checker_lines))
        try:
            result = subprocess.run([__import__("sys").executable, checker_path], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return [{"call": "(all)", "expected": "(all)", "actual": "<reference check timed out>"}]
        try:
            rows = json.loads(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else []
        except (ValueError, IndexError):
            return [{"call": "(all)", "expected": "(all)", "actual": "<checker script itself failed: %s>" % (result.stderr or result.stdout)[:300]}]
    return [{"call": r["call"], "expected": r["expected"], "actual": r["actual"]} for r in rows if not r.get("matches")]


def cross_check_test_suite(task_description, test_suite):
    """The real entry point: generates an independent reference implementation, extracts the
    test suite's simple checkable assertions, and reports any that disagree with the reference.
    Returns {"checked": int, "mismatches": [...]}. An empty mismatches list with checked=0 means
    nothing in this test suite matched the simple checkable pattern -- NOT proof of correctness,
    stated honestly rather than implied."""
    assertions = extract_simple_assertions(test_suite)
    if not assertions:
        return {"checked": 0, "mismatches": []}
    reference_code = generate_reference_implementation(task_description)
    if not reference_code:
        return {"checked": 0, "mismatches": []}
    mismatches = check_against_reference(reference_code, assertions)
    return {"checked": len(assertions), "mismatches": mismatches}
