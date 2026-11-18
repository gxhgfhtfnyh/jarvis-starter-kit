# -*- coding: utf-8 -*-
"""Milestone 5, first real step (op a_4e656055, Nov 13 2026): a standalone daemon that watches
a job queue and runs the full specialist+verifier pipeline on its own -- genuine independence
from me (Jarvis) specifically, not just "a function someone can call." Drop a job file in
QUEUE_DIR, the daemon picks it up, runs it, writes a result file, with zero further involvement
needed from me per task.

Trust boundary, explicit: this only processes job files that land in QUEUE_DIR, a local-only
directory. It is NOT exposed to the network or any external/untrusted input source -- job files
come from me or from the owner directly on this machine, same trust level as running a script by
hand. The task's test_suite field is executed via subprocess (same as every milestone so far),
so queue jobs are code-execution-equivalent to local scripts, not a remote-attacker surface.
"""
import json
import os
import time

import swarm_autonomous_loop as al
import swarm_coordination as coord
import swarm_milestone1 as sm
import swarm_milestone2 as sm2
import swarm_queue_filler as qf
import swarm_working_memory as wm

MAILBOX_ROLES = ("researcher", "coder", "verifier")

LESSONS_PATH = r"C:\LocalAI\swarm_memory\lessons.jsonl"
VAULT_PATH = r"C:\LocalAI\swarm_memory\vault.jsonl"
CONSOLIDATED_DIR = r"C:\LocalAI\swarm_memory\results_consolidated"

QUEUE_DIR = r"C:\LocalAI\swarm_memory\queue"
RESULTS_DIR = r"C:\LocalAI\swarm_memory\results"
PROCESSED_DIR = r"C:\LocalAI\swarm_memory\queue_processed"

# A queue job can request a specific role run on specific hardware/model via a plain string
# key (job JSON can't reference a Python tuple directly) -- resolved here rather than letting a
# job file name an arbitrary URL, so a malformed/malicious job can't point a role at an
# arbitrary network endpoint. "rig_granite" proves the rig-hosted model (confirmed live
# 2026-11-13) is genuinely usable from inside a real unattended daemon run, not just a one-off
# direct script call.
MODEL_ROUTING_KEYS = {
    "rig_granite": (sm.RIG_GRANITE_URL, "granite"),
    "local_qwen": (sm.LOCAL_QWEN_URL, "qwen"),
}


def list_pending_jobs(queue_dir=QUEUE_DIR):
    """Pure-ish (one real directory listing). Returns sorted job filenames waiting to run."""
    if not os.path.isdir(queue_dir):
        return []
    return sorted(f for f in os.listdir(queue_dir) if f.endswith(".json"))


def load_job(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run_job(job):
    """Runs one job's full specialist+verifier pipeline, uncontrolled -- same pattern as
    milestone 4's run_reduced_supervision, generalized to any task/test_suite a queue job
    supplies. Uses swarm_coordination's real pipeline (milestone 5 proper, Nov 13 2026): the
    coder can post a genuine help_request to the researcher's mailbox if it stays unconfident
    after its own retry, not just the plain self-confidence gate -- a real queued task gets the
    actual best pipeline, not the three-way A/B/C comparison milestone 1 runs for its own
    experiment. Wires in the working-memory layer: a fresh scratchpad for this task_id, entries
    added from the real specialist/verifier/peer output as the pipeline runs, evicted into the
    persistent lessons store at the end. Returns the result dict (name, passed, retried,
    seconds, detail)."""
    name = job["name"]
    task = job["task"]
    test_suite = job["test_suite"]
    memory_name = job.get("memory_name", "%s.txt" % name)
    memory_path = os.path.join(sm.SWARM_MEMORY_DIR, memory_name)
    wm.create_scratchpad(name)
    # Job-scoped only: applied right before the run and restored right after (even on
    # exception), so one job's hardware routing can never leak into the next job the daemon
    # picks up -- sm.ROLE_MODEL_URL is a shared module-level dict, not per-call state.
    role_routing = job.get("role_routing") or {}
    prior_routing = dict(sm.ROLE_MODEL_URL)
    for role, key in role_routing.items():
        if key in MODEL_ROUTING_KEYS:
            sm.ROLE_MODEL_URL[role] = MODEL_ROUTING_KEYS[key]
    try:
        os.makedirs(sm.SWARM_MEMORY_DIR, exist_ok=True)
        final = coord.run_task_for_daemon(memory_path, task, test_suite)
        if final.get("analysis"):
            wm.add_entry(name, "researcher flagged: %s" % final["analysis"][:200])
        if final.get("peer_consulted"):
            wm.add_entry(name, "peer (researcher) help: %s" % str(final.get("peer_reply_text"))[:200])
        if final.get("verdict"):
            wm.add_entry(name, "verifier verdict: %s" % str(final["verdict"])[:200])
        wm.add_entry(name, "outcome: passed=%s retried=%s" % (final["passed"], final.get("retried", False)))
        wm.evict_to_long_term(name, LESSONS_PATH, vault_path=VAULT_PATH)
        return {
            "name": name,
            "passed": final["passed"],
            "retried": final.get("retried", False),
            "seconds": final["seconds"],
            "detail": final["detail"],
            # Real gap found live 2026-11-14 (the owner, 5th ask: "agent thinking/code writing"
            # as its own visible block, not folded invisibly into the final pass/fail): the
            # actual generated code was computed right here and then silently discarded before
            # this point -- nothing downstream (saved result file, terminal/Discord reply) could
            # ever show it even if it wanted to.
            "code": final.get("code"),
            # Real observability gap found live 2026-11-13: without this, there was no way to
            # tell from a saved result file whether a real queued task organically triggered the
            # peer-help path short of grepping lessons.jsonl for the wm entry text.
            "peer_consulted": final.get("peer_consulted", False),
            "error": None,
        }
    except Exception as e:  # noqa: BLE001 -- a job must never crash the daemon
        wm.evict_to_long_term(name, LESSONS_PATH, vault_path=VAULT_PATH)
        return {
            "name": name,
            "passed": False,
            "retried": False,
            "seconds": 0,
            "detail": "",
            "error": "%s: %s" % (type(e).__name__, e),
        }
    finally:
        sm.ROLE_MODEL_URL.clear()
        sm.ROLE_MODEL_URL.update(prior_routing)


def process_one(queue_dir=QUEUE_DIR, results_dir=RESULTS_DIR, processed_dir=PROCESSED_DIR):
    """Processes the single oldest pending job, if any. Returns the result dict, or None if the
    queue was empty. Moves the job file to processed_dir afterward so it is never rerun."""
    pending = list_pending_jobs(queue_dir)
    if not pending:
        return None
    job_path = os.path.join(queue_dir, pending[0])
    job = load_job(job_path)
    result = run_job(job)

    os.makedirs(results_dir, exist_ok=True)
    result_path = os.path.join(results_dir, "%s_result.json" % job["name"])
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    os.makedirs(processed_dir, exist_ok=True)
    try:
        os.replace(job_path, os.path.join(processed_dir, pending[0]))
    except FileNotFoundError:
        # Real crash caught live 2026-11-13: something (a human, another process) removed the
        # job file after it was already picked up and run. The result was still produced and
        # saved above -- losing the daemon over a file that no longer needs moving would be
        # strictly worse than just noting it and moving on.
        pass
    return result


def _list_unconsolidated_results(results_dir=RESULTS_DIR, consolidated_dir=CONSOLIDATED_DIR):
    """Pure-ish (one real directory listing). Returns result filenames not yet consolidated."""
    if not os.path.isdir(results_dir):
        return []
    return sorted(f for f in os.listdir(results_dir) if f.endswith("_result.json"))


def _is_high_signal(result):
    """Pure. The episodic-memory-replay research's TD-error-like weighting, applied simply:
    a result that needed a retry or failed outright is the real 'surprise' signal worth
    consolidating into a lesson -- a clean first-try pass has nothing new to generalize from
    that milestone 2's real-time memory-writer wouldn't already have caught."""
    return bool(result.get("retried")) or not result.get("passed", True)


def consolidate_idle(results_dir=RESULTS_DIR, consolidated_dir=CONSOLIDATED_DIR, lessons_path=LESSONS_PATH):
    """The real episodic-memory/replay-research addition (Cognitive Architecture Theories for
    Kyogen, Nov 13 2026): runs only during idle time (watch_loop calls this when the queue is
    empty, never alongside live task processing), reviews recent high-signal results (retried
    or failed, not every clean pass), proposes ONE generalized lesson per high-signal result via
    the existing verified propose/verify/commit pipeline, and moves the result to
    consolidated_dir so it is never re-reviewed. Returns the number of results consolidated this
    pass (0 if nothing new or nothing high-signal)."""
    pending = _list_unconsolidated_results(results_dir, consolidated_dir)
    consolidated = 0
    for filename in pending:
        path = os.path.join(results_dir, filename)
        with open(path, encoding="utf-8") as f:
            result = json.load(f)
        if _is_high_signal(result):
            summary = "Task '%s' result: passed=%s retried=%s detail=%s" % (
                result.get("name"), result.get("passed"), result.get("retried"), str(result.get("detail"))[:300],
            )
            try:
                lesson = sm2.propose_lesson(result.get("name", "unknown"), summary)
                verdict = sm2.verify_lesson(lesson)
                if sm2.is_lesson_verified(verdict):
                    sm2.commit_lesson(lesson, lessons_path)
            except Exception:  # noqa: BLE001 -- real crash caught live 2026-11-14: this call had
                # no exception handling at all (a pre-existing gap, predating tonight's own
                # maybe_refill_queue/maybe_run_improvement_cycle additions, missed during that
                # same audit) and a brief local-model connection-refused window during a model
                # restart killed the entire daemon process outright, same "a job must never
                # crash the daemon" contract as run_job already follows. The result still moves
                # to consolidated_dir below so it's never retried in a loop over the same
                # failure -- losing one lesson proposal is acceptable, losing the daemon is not.
                pass
        os.makedirs(consolidated_dir, exist_ok=True)
        os.replace(path, os.path.join(consolidated_dir, filename))
        consolidated += 1
    return consolidated


QUEUE_REFILL_COOLDOWN_SECONDS = 300  # 2026-11-14: without this, an idle loop polling every 5s
# would re-attempt a refill (several real model calls each) on every single empty tick, hammering
# the local model for no benefit while genuinely idle. A real auto-refill replaces me manually
# running swarm_queue_filler.fill_queue() by hand every time I happened to notice the queue was
# empty (caught live the same night as the "force, don't remember" keep_going.py fix) -- this is
# the same principle applied to the daemon's own standing work, not just my Stop-hook behavior.
QUEUE_REFILL_BATCH = 4


def maybe_refill_queue(queue_dir, last_refill_time, now, cooldown_seconds=QUEUE_REFILL_COOLDOWN_SECONDS, n=QUEUE_REFILL_BATCH):
    """Pure-ish (the only side effect is the real fill_queue call itself): if the queue is
    genuinely empty AND at least cooldown_seconds have passed since the last refill attempt,
    generates up to n new self-directed jobs via swarm_queue_filler and returns the new
    last_refill_time. Otherwise returns last_refill_time unchanged -- callers use the return
    value to track state across polls without a module-level global."""
    try:
        has_jobs = bool(os.listdir(queue_dir))
    except OSError:
        return last_refill_time
    if has_jobs or (now - last_refill_time) < cooldown_seconds:
        return last_refill_time
    try:
        qf.fill_queue(queue_dir, n)
    except Exception:  # noqa: BLE001 -- same contract as run_job: idle-time maintenance must
        # never crash the daemon (a real gap found live 2026-11-14, minutes after writing this
        # exact principle into keep_going.py's own fix -- neither this nor the improvement-cycle
        # call below originally had it, despite run_job already establishing the pattern).
        return last_refill_time
    return now


IMPROVEMENT_CYCLE_COOLDOWN_SECONDS = 1800  # 2026-11-14 (the owner: "should not stop thinking and
# upgrading itself"): goal-2's real self-improvement cycle (pattern mining + self-directed
# practice) existed but only ever ran when I manually called run_improvement_cycle() by hand.
# A 30-minute cooldown, not a tight one, because one real cycle makes several genuine model
# calls (meta-review, task generation, task review, and -- if a pattern is found -- a full
# practice-task pipeline run), unlike the queue refill's single cheap generation pass.


def maybe_run_improvement_cycle(lessons_path, last_cycle_time, now, cooldown_seconds=IMPROVEMENT_CYCLE_COOLDOWN_SECONDS, field=None, config_path=None):
    """Runs one real goal-2 improvement cycle if cooldown_seconds have passed since the last
    one, and returns (ran: bool, result_or_None, new_last_cycle_time). Every real checkpoint
    inside run_improvement_cycle (independent pattern verification, independent test-suite
    review, the ground-truth cross-check) still applies unchanged -- this only decides WHEN a
    cycle is attempted, never skips or weakens what happens inside one. `field` defaults to
    None so each cycle classifies which role a detected pattern is actually about instead of
    always assuming coder (real gap fixed 2026-11-14)."""
    if (now - last_cycle_time) < cooldown_seconds:
        return False, None, last_cycle_time
    kwargs = {"field": field}
    if config_path is not None:
        kwargs["config_path"] = config_path
    try:
        result = al.run_improvement_cycle(lessons_path, **kwargs)
    except Exception as e:  # noqa: BLE001 -- same contract as run_job: a job must never crash
        # the daemon. An improvement cycle makes several real model calls across multiple
        # modules; any one of them failing transiently must not take down queue processing and
        # mailbox resolution along with it.
        return True, {"error": "%s: %s" % (type(e).__name__, e)}, now
    return True, result, now


VAULT_RECLASSIFY_COOLDOWN_SECONDS = 1800  # 2026-11-14 (built the vault, then its own docstring
# promise): classifying a memory's real category is a real judge call, not free -- a 30-minute
# cooldown, same as the improvement cycle, keeps the idle loop from spending a model call on
# this every single idle tick while the vault still fills in fast during real use.


def maybe_reclassify_vault(vault_path, last_run_time, now, cooldown_seconds=VAULT_RECLASSIFY_COOLDOWN_SECONDS):
    """Runs one real reclassification pass (kyogen_memory_vault.reclassify_general_entries) if
    cooldown_seconds have passed since the last one. Returns (ran: bool, changed_count,
    new_last_run_time) -- changed_count is 0 (not an error) when there was nothing left to
    reclassify, same honest-zero discipline as the rest of this module."""
    if (now - last_run_time) < cooldown_seconds:
        return False, 0, last_run_time
    import kyogen_memory_vault as kv
    try:
        changed = kv.reclassify_general_entries(vault_path)
    except Exception:  # noqa: BLE001 -- same contract as every other idle-time feature here:
        # a job must never crash the daemon. reclassify_general_entries makes a real model call
        # per entry; any one of them failing transiently must not take down queue processing.
        return True, 0, now
    return True, changed, now


def resolve_mailboxes_idle(roles=MAILBOX_ROLES, mailbox_dir=coord.mb.MAILBOX_DIR):
    """Milestone 5 proper: answers any pending peer help_requests sitting in any role's
    mailbox, on the daemon's own polling schedule -- decoupled from the moment a coder (or any
    role) posted the request. This is the real independent-timing piece: the requester does not
    block waiting for this, the daemon gets to it whenever it next polls. Returns the total
    number of requests resolved this pass."""
    total = 0
    for role in roles:
        try:
            total += coord.resolve_pending_mailbox(role, mailbox_dir=mailbox_dir)
        except Exception:  # noqa: BLE001 -- same real gap, same contract as consolidate_idle
            # above: coord.resolve_pending_mailbox makes a real model call
            # (resolve_help_request -> sm._call_role) with no exception handling of its own.
            # One role's transient failure must not block the others or crash the daemon.
            pass
    return total


def watch_loop(poll_seconds=5, queue_dir=QUEUE_DIR, results_dir=RESULTS_DIR, processed_dir=PROCESSED_DIR,
               consolidated_dir=CONSOLIDATED_DIR, lessons_path=LESSONS_PATH, vault_path=VAULT_PATH):
    """Runs forever: polls the queue, processes whatever shows up, no caller involvement needed
    per task. When the queue is genuinely empty (idle time), resolves any pending mailbox help
    requests first, then runs one offline consolidation pass -- both the real episodic-memory/
    replay-inspired addition and the real agent-to-agent coordination addition only ever run
    during idle time, never racing a live task."""
    print("swarm_daemon watching %s (poll every %ss)" % (queue_dir, poll_seconds), flush=True)
    last_refill_time = 0.0
    last_improvement_cycle_time = 0.0
    last_vault_reclassify_time = 0.0
    while True:
        result = process_one(queue_dir, results_dir, processed_dir)
        if result:
            print("processed %s: passed=%s retried=%s" % (result["name"], result["passed"], result["retried"]), flush=True)
        else:
            resolved = resolve_mailboxes_idle()
            if resolved:
                print("resolved %d pending mailbox help request(s) during idle time" % resolved, flush=True)
            n = consolidate_idle(results_dir, consolidated_dir, lessons_path)
            if n:
                print("consolidated %d result(s) during idle time" % n, flush=True)
            new_refill_time = maybe_refill_queue(queue_dir, last_refill_time, time.time())
            if new_refill_time != last_refill_time:
                print("auto-refilled the queue during idle time", flush=True)
            last_refill_time = new_refill_time
            ran, cycle_result, last_improvement_cycle_time = maybe_run_improvement_cycle(
                lessons_path, last_improvement_cycle_time, time.time())
            if ran and cycle_result.get("error"):
                print("improvement cycle failed, will retry next cooldown: %s" % cycle_result["error"], flush=True)
            elif ran:
                m1_found = bool(cycle_result["milestone1"].get("pattern_found"))
                m2_ran = cycle_result["milestone2"] is not None
                print("ran a goal-2 improvement cycle: pattern_found=%s practice_ran=%s" % (m1_found, m2_ran), flush=True)
            ran_reclassify, reclassified_count, last_vault_reclassify_time = maybe_reclassify_vault(
                vault_path, last_vault_reclassify_time, time.time())
            if ran_reclassify and reclassified_count:
                print("reclassified %d vault entr%s during idle time" % (
                    reclassified_count, "y" if reclassified_count == 1 else "ies"), flush=True)
        time.sleep(poll_seconds)


if __name__ == "__main__":
    try:
        watch_loop()
    except KeyboardInterrupt:
        pass
