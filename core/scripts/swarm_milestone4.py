# -*- coding: utf-8 -*-
"""Milestone 4 of the local specialist-swarm AGI experiment (op a_4e656055, Nov 13 2026):
reduced-supervision runs -- a full, NEW task end to end with only the final result spot-checked,
not every intermediate step supervised. The task itself (merge overlapping intervals) has never
been used in milestones 1-3, so this also tests generalization, not memorized behavior on a
task the harness was tuned against.

The real difference from milestone 1's condition C: there, I (the operator) read the full
analysis/verdict/code at every step while building and debugging the harness. Here, the swarm
runs condition C uncontrolled, and the only thing checked afterward is the final pass/fail from
the independent test suite -- the actual "spot-check the end result" pattern the milestone
ladder calls for.
"""
import swarm_milestone1 as sm

MERGE_INTERVALS_TASK = (
    "Implement a Python function merge_intervals(intervals: list[list[int]]) -> list[list[int]] "
    "that takes a list of [start, end] integer pairs (not necessarily sorted) and merges all "
    "overlapping intervals, returning the merged list sorted by start. Two intervals that only "
    "touch at an endpoint (e.g. [1,3] and [3,5]) should also be merged into one. An empty input "
    "list should return an empty list. Return ONLY the Python code in one ```python fenced "
    "block, no explanation."
)

MERGE_INTERVALS_TEST_SUITE = r"""
import sys
sys.path.insert(0, r"{code_dir}")
from candidate import merge_intervals

def test_basic_overlap():
    assert merge_intervals([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]

def test_unsorted_input():
    assert merge_intervals([[8, 10], [1, 3], [2, 6]]) == [[1, 6], [8, 10]]

def test_touching_endpoints_merge():
    assert merge_intervals([[1, 3], [3, 5]]) == [[1, 5]]

def test_empty_input():
    assert merge_intervals([]) == []

def test_single_interval():
    assert merge_intervals([[5, 7]]) == [[5, 7]]

def test_fully_contained_interval():
    assert merge_intervals([[1, 10], [2, 3]]) == [[1, 10]]

def test_no_overlap_stays_separate():
    assert merge_intervals([[1, 2], [5, 6], [10, 11]]) == [[1, 2], [5, 6], [10, 11]]

test_basic_overlap()
test_unsorted_input()
test_touching_endpoints_merge()
test_empty_input()
test_single_interval()
test_fully_contained_interval()
test_no_overlap_stays_separate()
print("ALL TESTS PASSED")
"""


def run_reduced_supervision(task=MERGE_INTERVALS_TASK, test_suite=MERGE_INTERVALS_TEST_SUITE, memory_name="milestone4_merge.txt"):
    """Runs the full specialist+verifier pipeline (condition C) on a genuinely new task,
    uncontrolled. Returns ONLY the spot-checkable summary a reduced-supervision operator would
    actually look at: did it pass, how many retries did it take, nothing about the intermediate
    reasoning content itself (that's still logged by run_all for a real audit trail, just not
    what this function's caller is meant to read to decide pass/fail)."""
    results = sm.run_all(memory_name, task=task, test_suite=test_suite)
    final = results[-1]  # condition C, the actual deliverable
    return {
        "passed": final["passed"],
        "retried": final.get("retried", False),
        "seconds": final["seconds"],
        "detail": final["detail"],
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run_reduced_supervision(), indent=2))
