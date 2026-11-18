"""Tests for unread_dm_gate's pure core."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import unread_dm_gate as udg  # noqa: E402

OWNER = udg.OWNER


def _line(**kw):
    base = {"id": "1", "author_id": OWNER, "source": "dm", "text": "hi"}
    base.update(kw)
    return (json.dumps(base) + "\n").encode("utf-8")


def test_finds_a_single_unread_dm_from_owner():
    chunk = _line(id="111")
    assert udg.find_unread_dm_ids(chunk) == ["111"]


def test_finds_multiple_unread_dms_in_order():
    chunk = _line(id="1") + _line(id="2") + _line(id="3")
    assert udg.find_unread_dm_ids(chunk) == ["1", "2", "3"]


def test_ignores_messages_from_someone_else():
    chunk = _line(id="1", author_id=999) + _line(id="2")
    assert udg.find_unread_dm_ids(chunk) == ["2"]


def test_ignores_non_dm_source():
    chunk = _line(id="1", source="channel") + _line(id="2")
    assert udg.find_unread_dm_ids(chunk) == ["2"]


def test_empty_chunk_is_empty():
    assert udg.find_unread_dm_ids(b"") == []


def test_ignores_blank_lines_and_bad_json():
    chunk = b"\n" + b"not json\n" + _line(id="1")
    assert udg.find_unread_dm_ids(chunk) == ["1"]


def test_unread_chunk_none_when_cursor_missing(tmp_path, monkeypatch):
    log = tmp_path / "discord_incoming.jsonl"
    log.write_bytes(_line(id="1"))
    monkeypatch.setattr(udg, "LOG", str(log))
    monkeypatch.setattr(udg, "CUR", str(tmp_path / "no_such_cursor.txt"))
    assert udg._unread_chunk() is None


def test_unread_chunk_none_when_caught_up(tmp_path, monkeypatch):
    log = tmp_path / "discord_incoming.jsonl"
    data = _line(id="1")
    log.write_bytes(data)
    cur = tmp_path / "cursor.txt"
    cur.write_text(str(len(data)), encoding="utf-8")
    monkeypatch.setattr(udg, "LOG", str(log))
    monkeypatch.setattr(udg, "CUR", str(cur))
    assert udg._unread_chunk() is None


def test_unread_chunk_returns_bytes_past_cursor(tmp_path, monkeypatch):
    log = tmp_path / "discord_incoming.jsonl"
    first, second = _line(id="1"), _line(id="2")
    log.write_bytes(first + second)
    cur = tmp_path / "cursor.txt"
    cur.write_text(str(len(first)), encoding="utf-8")
    monkeypatch.setattr(udg, "LOG", str(log))
    monkeypatch.setattr(udg, "CUR", str(cur))
    assert udg.find_unread_dm_ids(udg._unread_chunk()) == ["2"]


def test_main_is_a_noop_with_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(udg, "OFF", str(tmp_path / "off.txt"))
    open(udg.OFF, "w").close()
    try:
        udg.main()  # must not raise even with no real log/cursor files on this machine
    finally:
        os.remove(udg.OFF)
