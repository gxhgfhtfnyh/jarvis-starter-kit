# -*- coding: utf-8 -*-
"""Security review (2026-11-18): route_job.py's decision log wrote the first 300 chars of the task text verbatim
even when the job was classified private_boss, so sensitive task content could land in a plaintext log file."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import route_job as rj  # noqa: E402


def test_main_redacts_the_task_text_in_the_log_for_a_private_job(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rj, "LOG", tmp_path / "route_job.jsonl")
    monkeypatch.setattr(sys, "argv", ["route_job.py", "--name", "job1", "--task", "my real credit card is 4111111111111111"])
    monkeypatch.setattr(rj, "route", lambda task, code_only=False, folder=None: {"answer": "private_boss", "confidence": 1.0, "via": "keyword"})
    rj.main()
    row = json.loads((tmp_path / "route_job.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert "4111111111111111" not in row["task"]
    assert row["task"] == "<redacted: private_boss>"


def test_main_keeps_the_task_text_in_the_log_for_a_non_private_job(tmp_path, monkeypatch):
    monkeypatch.setattr(rj, "LOG", tmp_path / "route_job.jsonl")
    monkeypatch.setattr(sys, "argv", ["route_job.py", "--name", "job1", "--task", "write a test for parse_csv"])
    monkeypatch.setattr(rj, "route", lambda task, code_only=False, folder=None: {"answer": "easy_worker", "confidence": 0.9, "via": "model"})
    rj.main()
    row = json.loads((tmp_path / "route_job.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["task"] == "write a test for parse_csv"
