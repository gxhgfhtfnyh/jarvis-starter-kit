# -*- coding: utf-8 -*-
"""A real, hidden-answer coding test set for comparing local models/configs without sacrificing
quality -- rebuilt 2026-11-02 after the original (2026-11-01, a different session's scratchpad)
wasn't recoverable. Each task has a prompt, a reference solution, and a set of real assertions the
model's OWN generated code must pass when actually executed -- not graded by a second LLM's opinion,
by running the code for real.

ISOLATION, fixed 2026-11-02 after an automated security review correctly flagged the first version:
an in-process exec() with a trimmed __builtins__ dict is NOT a real sandbox -- it is routinely
escapable through plain Python introspection (e.g. reaching arbitrary classes through
object.__subclasses__() without ever touching a blocked name directly), review findings explicitly
say not to treat it as one even for an "internal-only" use like this. grade_one() now runs each
candidate in a genuinely separate, throwaway OS process (hard_test_runner.py, launched with -I and
a hard timeout) instead of this process's own live interpreter -- not a full container/seccomp
sandbox (no such tooling on this Windows PC), but an escape now lands in a disposable child process,
not this session's own state. The restricted builtins stay on in that child as a second, cheaper
layer, not the only one.

Usage:
    from hard_test_set import TASKS, grade_one
    result = grade_one(TASKS[0], model_generated_code_string)
"""
import collections
import functools
import itertools
import json
import math
import re as re_module
import subprocess
import sys
from pathlib import Path

# Real models write real, idiomatic code -- blocking ALL imports made the first real test run
# reject a correct functools.lru_cache memoized fibonacci as a false negative (found live
# 2026-11-02). Only these modules are genuinely safe (pure computation, no I/O) and a
# restricted __import__ refuses everything else rather than exposing the real one.
_SAFE_MODULES = {"math": math, "functools": functools, "itertools": itertools,
                  "collections": collections, "re": re_module}


def _safe_import(name, *args, **kwargs):
    if name in _SAFE_MODULES:
        return _SAFE_MODULES[name]
    raise ImportError("module '%s' is not in the sandbox's safe allowlist" % name)


def _check_fib(ns):
    def fib(n):
        return ns["fib"](n)
    return fib(0) == 0 and fib(1) == 1 and fib(10) == 55 and fib(20) == 6765


def _check_is_prime(ns):
    f = ns["is_prime"]
    return (not f(1)) and f(2) and f(17) and (not f(18)) and f(97) and (not f(100))


def _check_reverse_words(ns):
    f = ns["reverse_words"]
    return f("hello world") == "world hello" and f("a") == "a" and f("") == ""


def _check_binary_search(ns):
    f = ns["binary_search"]
    arr = [1, 3, 5, 7, 9, 11]
    return f(arr, 7) == 3 and f(arr, 1) == 0 and f(arr, 12) == -1 and f(arr, 2) == -1


def _check_flatten(ns):
    f = ns["flatten"]
    return f([1, [2, 3], [4, [5, 6]], 7]) == [1, 2, 3, 4, 5, 6, 7] and f([]) == []


def _check_gcd(ns):
    f = ns["gcd"]
    return f(12, 18) == 6 and f(17, 5) == 1 and f(0, 5) == 5 and f(100, 75) == 25


def _check_word_count(ns):
    f = ns["word_count"]
    r = f("the cat sat on the mat the cat ran")
    return r.get("the") == 3 and r.get("cat") == 2 and r.get("sat") == 1


def _check_is_palindrome(ns):
    f = ns["is_palindrome"]
    return f("racecar") and f("a") and (not f("hello")) and f("A man a plan a canal Panama".replace(" ", "").lower())


def _check_merge_intervals(ns):
    f = ns["merge_intervals"]
    return f([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]


def _check_quicksort(ns):
    f = ns["quicksort"]
    return f([5, 2, 9, 1, 5, 6]) == [1, 2, 5, 5, 6, 9] and f([]) == [] and f([1]) == [1]


# Each task: id, a real prompt (what we'd actually send the model), the function name the solution
# must define, and a real assertion-based checker (not string/LLM-judged).
TASKS = [
    {"id": "fib", "prompt": "Write a Python function `fib(n)` that returns the nth Fibonacci number (fib(0)=0, fib(1)=1), using memoization.", "check": _check_fib},
    {"id": "is_prime", "prompt": "Write a Python function `is_prime(n)` that returns True if n is a prime number, False otherwise.", "check": _check_is_prime},
    {"id": "reverse_words", "prompt": "Write a Python function `reverse_words(s)` that reverses the order of words in a string s, keeping each word itself unchanged.", "check": _check_reverse_words},
    {"id": "binary_search", "prompt": "Write a Python function `binary_search(arr, target)` that returns the index of target in sorted list arr, or -1 if not found.", "check": _check_binary_search},
    {"id": "flatten", "prompt": "Write a Python function `flatten(lst)` that flattens an arbitrarily nested list of integers into a single flat list, in order.", "check": _check_flatten},
    {"id": "gcd", "prompt": "Write a Python function `gcd(a, b)` that returns the greatest common divisor of two non-negative integers a and b.", "check": _check_gcd},
    {"id": "word_count", "prompt": "Write a Python function `word_count(s)` that returns a dict mapping each word in string s to how many times it appears.", "check": _check_word_count},
    {"id": "is_palindrome", "prompt": "Write a Python function `is_palindrome(s)` that returns True if string s reads the same forwards and backwards, False otherwise.", "check": _check_is_palindrome},
    {"id": "merge_intervals", "prompt": "Write a Python function `merge_intervals(intervals)` that merges overlapping intervals (each a [start, end] list) and returns the merged list, sorted.", "check": _check_merge_intervals},
    {"id": "quicksort", "prompt": "Write a Python function `quicksort(arr)` that returns a new sorted list using the quicksort algorithm.", "check": _check_quicksort},
]


def _exec_and_check(task, code):
    """The actual exec+check, run inside the throwaway child process (hard_test_runner.py) -- never
    call this directly from the main session's own process, see the module docstring for why."""
    ns = {"__builtins__": {"range": range, "len": len, "list": list, "dict": dict, "set": set,
                            "sorted": sorted, "min": min, "max": max, "sum": sum, "abs": abs,
                            "int": int, "float": float, "str": str, "bool": bool, "enumerate": enumerate,
                            "isinstance": isinstance, "True": True, "False": False, "None": None,
                            "math": math, "reversed": reversed, "zip": zip, "map": map, "filter": filter,
                            "__import__": _safe_import, "tuple": tuple, "Exception": Exception}}
    try:
        exec(code, ns)  # noqa: S102 -- throwaway child process, second-layer restricted builtins too
    except Exception as e:
        return False, "exec failed: %s: %s" % (type(e).__name__, e)
    try:
        ok = bool(task["check"](ns))
    except Exception as e:
        return False, "check failed: %s: %s" % (type(e).__name__, e)
    return ok, None if ok else "assertions failed"


_RUNNER = Path(__file__).resolve().parent / "hard_test_runner.py"


def grade_one(task, code, timeout_s=15):
    """Runs `code` against task's real checker in a genuinely separate OS process (see module
    docstring for why this isn't done in-process). Returns (passed: bool, error: str|None). A
    timeout, a crash, or malformed output from the child all count as a clean fail, never a raise."""
    try:
        proc = subprocess.run(
            [sys.executable, "-I", str(_RUNNER), task["id"]],
            input=json.dumps({"code": code}), capture_output=True, text=True, timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return False, "timed out after %ss (possible infinite loop in generated code)" % timeout_s
    if proc.returncode != 0 and not proc.stdout.strip():
        return False, "runner process failed: %s" % (proc.stderr or "no output")[:300]
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:
        return False, "could not parse runner output: %s (%s)" % (e, proc.stdout[:200])
    return bool(result.get("ok")), result.get("error")


def grade_all(code_by_task_id):
    """code_by_task_id: {task_id: code_string}. -> {task_id: (passed, error)}, plus a pass count."""
    results = {}
    passed = 0
    for t in TASKS:
        code = code_by_task_id.get(t["id"])
        if code is None:
            results[t["id"]] = (False, "no code provided")
            continue
        ok, err = grade_one(t, code)
        results[t["id"]] = (ok, err)
        passed += 1 if ok else 0
    return results, passed
