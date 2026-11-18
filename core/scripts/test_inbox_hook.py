"""Tests for inbox_hook.main(): hands me new owner DMs between tool calls (PostToolUse hook).
Run: python -m pytest -q test_inbox_hook.py
All paths are monkeypatched to tmp_path; nothing real is read or written (except the focus file read, which is optional)."""
import io
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inbox_hook as ih  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(ih, "LOG", str(tmp_path / "in.jsonl"))
    monkeypatch.setattr(ih, "CUR", str(tmp_path / "cur.txt"))
    monkeypatch.setattr(ih, "OFF", str(tmp_path / "off.txt"))
    (tmp_path / "in.jsonl").write_text(json.dumps({"id": 1, "author_id": ih.OWNER, "source": "dm", "text": "old"}) + "\n", encoding="utf-8")
    return tmp_path


def run(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    ih.main()
    return capsys.readouterr().out


def add(env, **m):
    with open(env / "in.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(m) + "\n")


def test_first_run_is_silent_and_sets_cursor(env, monkeypatch, capsys):
    assert run(monkeypatch, capsys) == ""
    assert int((env / "cur.txt").read_text()) == os.path.getsize(env / "in.jsonl")


def test_new_owner_dm_is_printed_once(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    add(env, id=2, author_id=ih.OWNER, source="dm", text="hello while working")
    out = run(monkeypatch, capsys)
    ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "hello while working" in ctx and "SCOPE LOCK" in ctx
    assert run(monkeypatch, capsys) == ""            # nothing new the second time


def test_other_author_or_source_ignored(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    add(env, id=3, author_id=999, source="dm", text="someone else")
    add(env, id=4, author_id=ih.OWNER, source="guild", text="group chatter")
    assert run(monkeypatch, capsys) == ""


def test_kill_switch(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    add(env, id=5, author_id=ih.OWNER, source="dm", text="should not show")
    (env / "off.txt").write_text("x")
    assert run(monkeypatch, capsys) == ""


def test_rotated_log_resets_without_error(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    (env / "cur.txt").write_text("999999")           # cursor beyond the end of the log
    assert run(monkeypatch, capsys) == ""
    assert int((env / "cur.txt").read_text()) == os.path.getsize(env / "in.jsonl")  # reset is persisted
    add(env, id=8, author_id=ih.OWNER, source="dm", text="arrives after rotation")
    out = run(monkeypatch, capsys)                   # and messages after the rotation are delivered (was a real bug)
    assert "arrives after rotation" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_corrupt_line_is_skipped(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    with open(env / "in.jsonl", "a", encoding="utf-8") as f:
        f.write("{not json\n")
    add(env, id=6, author_id=ih.OWNER, source="dm", text="after the corrupt line")
    out = run(monkeypatch, capsys)
    assert "after the corrupt line" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_attachment_only_message_still_shown(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    add(env, id=7, author_id=ih.OWNER, source="dm", text="")
    out = run(monkeypatch, capsys)
    assert "[attachment]" in json.loads(out)["hookSpecificOutput"]["additionalContext"]


def test_several_new_messages_are_all_shown_up_to_five(env, monkeypatch, capsys):
    run(monkeypatch, capsys)
    for i in range(7):
        add(env, id=100 + i, author_id=ih.OWNER, source="dm", text="burst message %d" % i)
    ctx = json.loads(run(monkeypatch, capsys))["hookSpecificOutput"]["additionalContext"]
    assert "burst message 6" in ctx and "burst message 2" in ctx   # the newest five
    assert "burst message 1" not in ctx and "burst message 0" not in ctx   # older ones dropped, by design


def test_subagent_tool_calls_are_recognised_so_they_never_receive_the_inbox():
    import inbox_hook
    assert inbox_hook.is_subagent_call({"agent_id": "a1ddb70f", "tool_name": "WebFetch"}) is True
    assert inbox_hook.is_subagent_call({"agent_type": "lab-researcher"}) is True
    assert inbox_hook.is_subagent_call({"tool_name": "Bash", "session_id": "x"}) is False
    assert inbox_hook.is_subagent_call({}) is False and inbox_hook.is_subagent_call(None) is False
