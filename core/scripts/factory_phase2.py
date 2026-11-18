# -*- coding: utf-8 -*-
"""Phase 2 of the Kyogen pivot: planner + reviewer roles around Phase 1's implementer
(factory_wrapper.run_agent), built because Finding 3 (the research paper, Case Study
§5.1) showed a single unsupervised local-model pass does not reliably meet a written
spec, even when given the exact deviations from a prior failed attempt. the owner's
go-ahead: Discord 2026-11-16 11:48, "go ahead anyways im not home build the whole thing
out without stopping."

Architecture ("agent proposes, code disposes", same security posture as Phase 1):
    plan()     -- text-only agent call (no tools at all), turns a short task description
                  into a detailed, numbered spec: exact function signature(s), exact
                  behavior, edge cases, and the tests required. No files touched.
    implement  -- Phase 1's existing factory_wrapper.run_agent + enforce(), unchanged.
    review()   -- text-only agent call (no tools at all) that reads the spec and the
                  actual kept file contents (not a description of them) and returns a
                  strict PASS/FAIL verdict plus a specific, actionable reason. Like
                  plan(), this NEVER executes the implementer's code -- the same "no
                  sandbox on this machine" constraint that made Phase 1 refuse to
                  auto-run tests applies here too: a reviewer that ran the code to judge
                  it would reintroduce exactly the arbitrary-code-execution risk Phase 1
                  removed. Review is static (spec vs. source text), not dynamic.
    run_phase2_cycle() -- orchestrates plan -> implement -> review, with a bounded retry
                  on FAIL (max 2 implementer attempts total, matching the same
                  retry-bounded-at-2 pattern already used by the intent store's own
                  task lifecycle, §4 of the paper): a FAIL's reason is appended to the
                  task text verbatim for the retry, the same way the real Finding-3
                  negative result explicitly gave the model its own prior deviations
                  and it still did not converge -- this orchestrator is the proposed
                  fix for exactly that failure mode, not a different thing.

Partially closed 2026-11-17 (the owner: "produce quality code with the proper checks"): static_check()
runs script_doctor.py's deterministic static analysis (syntax errors, mangled escapes, code_doctor's own
checks) on every kept .py file BEFORE the LLM reviewer ever sees it. A real finding is an automatic FAIL
with the exact findings as the reason -- no LLM call needed, since a syntax error or a mangled path is
objectively true, not a judgment call the reviewer could get wrong. Still a known, NOT fixed gap: this
only catches what static analysis catches (it cannot know the implementer actually met the SPEC, only that
the code parses and has no known bad patterns) -- the reviewer's text-only judgment on spec-compliance
remains a second opinion, not a guarantee, same honest line as before.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

PI_EXE = shutil.which("pi") or "pi"
MAX_IMPLEMENT_ATTEMPTS = 2  # matches intent_store's own bounded-retry-at-2 convention


def _run_text_only_agent(prompt, provider, model, cwd, timeout=300):
    """A pure text-generation call: `--no-tools` disables every built-in and extension
    tool pi exposes (not a hardcoded name list, which could drift out of sync with pi's
    own tool set), so plan() and review() can never touch the filesystem, network, or
    run code, regardless of what the prompt asks for. Returns (returncode, stdout, stderr).

    The prompt is written to a temp file and passed as `@<path>`, never inline: a real
    bug (2026-11-16) found pi's Windows .CMD wrapper silently mangles a multi-line
    string passed as a raw argument, so the agent receives effectively nothing of a
    real multi-paragraph prompt, with no error raised anywhere. Confirmed directly by
    reproducing it (a one-line prompt worked, an otherwise-identical multi-line one did
    not) and by confirming `@file` reads the full content back correctly."""
    fd, prompt_path = tempfile.mkstemp(suffix=".txt", prefix="factory_phase2_prompt_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        cmd = [PI_EXE, "--provider", provider, "--model", model, "--print", "--no-session",
               "--no-tools", "@" + prompt_path]
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        try:
            os.remove(prompt_path)
        except OSError:
            pass


PLAN_PREAMBLE = (
    "You are the PLANNER in a two-stage pipeline. You do not write code. Given the task "
    "below, produce a detailed, numbered specification an implementer will follow exactly: "
    "the exact function signature(s) (name, parameters, return type), the exact behavior "
    "for the normal case, every edge case you can identify, and the exact test cases "
    "required (inputs and expected outputs). Be precise enough that two different "
    "implementers would produce functionally identical code. Output only the numbered "
    "specification, nothing else.\n\nTASK:\n"
)

REVIEW_PREAMBLE = (
    "You are the REVIEWER in a two-stage pipeline. You do not write or run code. Below is "
    "a specification and the actual file contents an implementer produced. Check the "
    "source against the specification line by line: correct function signature, correct "
    "behavior for the normal case, every edge case from the spec actually handled, and the "
    "exact required tests actually present. Do not execute anything; judge from the text "
    "alone. Respond with exactly one line starting with either 'VERDICT: PASS' or "
    "'VERDICT: FAIL', then, only if FAIL, a specific, actionable list of exactly what does "
    "not match the spec (quote the spec line and what the code does instead).\n\n"
    "SPECIFICATION:\n{spec}\n\nIMPLEMENTER'S FILES:\n{files}\n"
)


def plan(task_text, provider, model, cwd):
    """-> spec_text (str). Raises RuntimeError on a non-zero exit or empty output, since a
    blank spec would silently make implement()'s task meaningless."""
    rc, out, err = _run_text_only_agent(PLAN_PREAMBLE + task_text, provider, model, cwd)
    if rc != 0:
        raise RuntimeError("planner exited %d: %s" % (rc, err.strip() or out.strip()))
    spec = out.strip()
    if not spec:
        raise RuntimeError("planner produced an empty specification")
    return spec


def _read_kept_files(watch_dir, kept):
    """-> {rel_path: contents}. kept is the (kind, rel_path) list enforce() returns; only
    the paths, read fresh from disk, never the agent's own self-report of what it wrote."""
    out = {}
    for _, rel in kept:
        full = os.path.join(watch_dir, rel)
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                out[rel] = f.read()
        except OSError as e:
            out[rel] = "<could not read: %s>" % e
    return out


def _format_files(files):
    return "\n\n".join("--- %s ---\n%s" % (rel, text) for rel, text in sorted(files.items()))


VERDICT_RE = re.compile(r"^VERDICT:\s*(PASS|FAIL)\b", re.I | re.M)


def static_check(files):
    """files: {rel_path: contents}. -> (passed: bool, reason: str). Runs script_doctor's deterministic
    checks on every .py file; a real finding is an objective FAIL, no model call needed. Never executes
    anything (script_doctor.check_source is pure AST/text analysis). Local import: keeps plan()/review()
    importable without script_doctor's own deps during unit tests that don't need this path."""
    import script_doctor
    problems = []
    for rel, text in sorted(files.items()):
        if not rel.endswith(".py"):
            continue
        findings = script_doctor.check_source(text, name=rel)
        for kind, line, message in findings:
            problems.append("%s:%d [%s] %s" % (rel, line, kind, message))
    if problems:
        return False, "static analysis found real issues:\n" + "\n".join(problems)
    return True, ""


def review(spec_text, files, provider, model, cwd):
    """files: {rel_path: contents}, from _read_kept_files. -> (passed: bool, reason: str).
    A verdict line that isn't found at all is treated as FAIL with that fact stated as the
    reason, rather than silently defaulting to PASS -- an ambiguous reviewer response must
    never be read as approval."""
    prompt = REVIEW_PREAMBLE.format(spec=spec_text, files=_format_files(files))
    rc, out, err = _run_text_only_agent(prompt, provider, model, cwd)
    if rc != 0:
        return False, "reviewer exited %d: %s" % (rc, err.strip() or out.strip())
    m = VERDICT_RE.search(out)
    if not m:
        return False, "reviewer produced no parseable VERDICT line; raw output: " + out.strip()[:500]
    passed = m.group(1).upper() == "PASS"
    reason = out[m.end():].strip() if not passed else ""
    return passed, reason


def run_phase2_cycle(task_text, write_scope, watch_dir, provider="local-qwen", model="qwen",
                      log=None, plan_provider=None, plan_model=None,
                      review_provider=None, review_model=None):
    """The full plan -> implement -> review loop, bounded retry on FAIL. Returns a dict
    with every attempt's real outcome, never just the final one, so a bounded-retry
    failure is as inspectable as a success. `log` is an optional callable(str) for
    progress lines; defaults to a no-op so this stays unit-testable without stdout
    coupling.

    provider/model is always the IMPLEMENT (builder) step's model -- the heavy one, since
    that's the only step that actually writes code. plan_provider/plan_model and
    review_provider/review_model default to provider/model (old behaviour, one model for
    everything) but can be pointed at a lighter model: per the build plan's own design
    (Kyogen Pivot doc, Phase 2 section), the planner/reviewer steps are smaller, bounded
    text-only judgments a lighter model can likely handle at a fraction of the RAM cost,
    reserving the heavy model for the one step that actually needs it. Real bug found
    2026-11-17: this was never wired in before now -- every role silently used the same
    model regardless of what was passed, so the plan's stated hardware-efficiency design
    was never actually in effect."""
    log = log or (lambda s: None)
    plan_provider = plan_provider or provider
    plan_model = plan_model or model
    review_provider = review_provider or provider
    review_model = review_model or model
    from factory_wrapper import snapshot, run_agent, enforce  # local import: keeps plan()/review() importable without pi on PATH during unit tests

    spec = plan(task_text, plan_provider, plan_model, watch_dir)
    log("PLAN:\n" + spec)

    attempts = []
    task_for_attempt = task_text + "\n\nSPECIFICATION (follow exactly):\n" + spec
    for attempt_n in range(1, MAX_IMPLEMENT_ATTEMPTS + 1):
        before = snapshot(watch_dir)
        rc, out, err = run_agent(task_for_attempt, provider, model, watch_dir)
        after = snapshot(watch_dir)
        violations, kept = enforce(watch_dir, write_scope, before, after)
        files = _read_kept_files(watch_dir, kept)
        passed, reason = static_check(files)
        if passed:
            passed, reason = review(spec, files, review_provider, review_model, watch_dir)

        attempts.append({
            "attempt": attempt_n, "agent_exit_code": rc, "violations": violations,
            "kept": kept, "review_passed": passed, "review_reason": reason,
        })
        log("ATTEMPT %d: kept=%s violations=%s review=%s" %
            (attempt_n, kept, violations, "PASS" if passed else "FAIL: " + reason))

        if passed:
            return {"status": "passed", "spec": spec, "attempts": attempts}
        if attempt_n < MAX_IMPLEMENT_ATTEMPTS:
            task_for_attempt = (
                task_text + "\n\nSPECIFICATION (follow exactly):\n" + spec +
                "\n\nYour previous attempt did NOT meet spec. Specific reviewer feedback, "
                "do not repeat these mistakes:\n" + reason
            )

    return {"status": "failed_after_retries", "spec": spec, "attempts": attempts}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--write-scope", action="append", required=True)
    ap.add_argument("--watch-dir", required=True)
    ap.add_argument("--provider", default="local-qwen")
    ap.add_argument("--model", default="qwen")
    ap.add_argument("--plan-provider", default=None, help="defaults to --provider if not set")
    ap.add_argument("--plan-model", default=None, help="defaults to --model if not set")
    ap.add_argument("--review-provider", default=None, help="defaults to --provider if not set")
    ap.add_argument("--review-model", default=None, help="defaults to --model if not set")
    args = ap.parse_args()

    with open(args.task_file, "r", encoding="utf-8") as f:
        task_text = f.read()

    result = run_phase2_cycle(task_text, args.write_scope, args.watch_dir,
                               args.provider, args.model, log=print,
                               plan_provider=args.plan_provider, plan_model=args.plan_model,
                               review_provider=args.review_provider, review_model=args.review_model)
    print("\nFINAL STATUS:", result["status"])
    print("Nothing was executed at any point in this cycle. Read the kept file(s) "
          "yourself before running anything in them.")
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
