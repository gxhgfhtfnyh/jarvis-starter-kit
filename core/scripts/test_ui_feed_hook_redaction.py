# -*- coding: utf-8 -*-
"""Security review (2026-11-18): ui_feed_hook.py logged Edit diffs (and the one-line "detail" for every tool call)
with no redaction at all, so a secret-shaped value touched by any tool call could land in the plaintext UI feed
log, and a file whose name alone says it holds credentials (.env, a *key*/*secret*/*token* file) was logged just
like any other file."""
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import ui_feed_hook as ufh  # noqa: E402


def _run_main(monkeypatch, tmp_path, payload):
    monkeypatch.setattr(ufh, "FEED_LOG", tmp_path / "feed.jsonl")
    monkeypatch.setattr(sys, "argv", ["ui_feed_hook.py", "pre"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    ufh.main()
    lines = (tmp_path / "feed.jsonl").read_text(encoding="utf-8").strip().splitlines()
    return json.loads(lines[-1]) if lines else {}


def test_a_secret_shaped_value_in_an_edit_diff_is_redacted(tmp_path, monkeypatch):
    payload = {"tool_name": "Edit", "tool_input": {
        "file_path": str(tmp_path / "config.py"),
        "old_string": "API_KEY = None",
        "new_string": 'API_KEY = "sk-abcdefghijklmnopqrstuvwx"',
    }}
    entry = _run_main(monkeypatch, tmp_path, payload)
    assert "sk-abcdefghijklmnopqrstuvwx" not in entry.get("diff", "")


def test_an_edit_to_an_env_file_logs_no_diff_at_all(tmp_path, monkeypatch):
    payload = {"tool_name": "Edit", "tool_input": {
        "file_path": str(tmp_path / ".env"),
        "old_string": "TOKEN=old",
        "new_string": "TOKEN=sk-abcdefghijklmnopqrstuvwx",
    }}
    entry = _run_main(monkeypatch, tmp_path, payload)
    assert "diff" not in entry


def test_an_edit_to_a_named_secret_file_logs_no_diff_at_all(tmp_path, monkeypatch):
    payload = {"tool_name": "Edit", "tool_input": {
        "file_path": str(tmp_path / "api_secret.json"),
        "old_string": "a",
        "new_string": "b",
    }}
    entry = _run_main(monkeypatch, tmp_path, payload)
    assert "diff" not in entry


def test_a_secret_shaped_value_in_the_detail_line_is_redacted(tmp_path, monkeypatch):
    payload = {"tool_name": "Bash", "tool_input": {"command": 'export API_KEY="sk-abcdefghijklmnopqrstuvwx"'}}
    entry = _run_main(monkeypatch, tmp_path, payload)
    assert "sk-abcdefghijklmnopqrstuvwx" not in entry.get("detail", "")


def test_an_ordinary_edit_still_logs_its_real_diff(tmp_path, monkeypatch):
    payload = {"tool_name": "Edit", "tool_input": {
        "file_path": str(tmp_path / "app.py"),
        "old_string": "x = 1",
        "new_string": "x = 2",
    }}
    entry = _run_main(monkeypatch, tmp_path, payload)
    assert "x = 2" in entry.get("diff", "")
