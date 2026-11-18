# -*- coding: utf-8 -*-
"""Tests for the orphan archiver's pure core and a full move-and-restore round trip in a temp folder."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import archive_orphans as ao  # noqa: E402


def test_select_keeps_only_old_unprotected_non_test_files():
    ages = {"old.py": 30, "new.py": 0.2, "edge.py": 3, "prot.py": 40, "test_x.py": 40}
    got = ao.select(["old.py", "new.py", "edge.py", "prot.py", "test_x.py", "unknown.py"], ages, protected={"prot.py"})
    assert got == ["edge.py", "old.py"]                                  # 3 days exactly counts; unknown age is kept back


def test_select_handles_empty_input():
    assert ao.select(None, {}, set()) == [] and ao.select([], {}, set()) == []


def test_with_tests_adds_only_tests_that_exist_and_does_not_duplicate():
    assert ao.with_tests(["a.py", "b.py"], {"test_a.py", "c.py"}) == ["a.py", "b.py", "test_a.py"]
    assert ao.with_tests(["a.py", "a.py"], {"test_a.py"}) == ["a.py", "test_a.py"]


def test_apply_then_restore_round_trip_deletes_nothing(tmp_path, monkeypatch, capsys):
    scripts, logs, arch = tmp_path / "scripts", tmp_path / "logs", tmp_path / "archive"
    scripts.mkdir(), logs.mkdir()
    for n in ("old_tool.py", "test_old_tool.py", "keep.py", "named.py"):
        (scripts / n).write_text("print(%r)" % n, encoding="utf-8")
    old = 1_000_000_000
    for n in ("old_tool.py", "test_old_tool.py", "named.py"):
        os.utime(scripts / n, (old, old))
    (logs / "harness_manifest.json").write_text(json.dumps({"classes": {"orphan": ["old_tool.py", "keep.py", "named.py"]}}), encoding="utf-8")
    (logs / "somecfg.json").write_text('{"run": "named.py"}', encoding="utf-8")
    monkeypatch.setattr(ao, "HERE", str(scripts))
    monkeypatch.setattr(ao, "LOGS", str(logs))
    monkeypatch.setattr(ao, "ARCHIVE", str(arch))
    monkeypatch.setattr(ao, "_protected", lambda names: {"named.py"})
    assert ao.main([]) == 0
    assert "Nothing moved" in capsys.readouterr().out and (scripts / "old_tool.py").exists()
    assert ao.main(["--apply"]) == 0
    out = capsys.readouterr().out
    assert "moved 2 file(s)" in out
    assert not (scripts / "old_tool.py").exists() and not (scripts / "test_old_tool.py").exists()
    assert (scripts / "keep.py").exists() and (scripts / "named.py").exists()           # too new / named in a config: untouched
    folder = [p for p in arch.iterdir()][0]
    assert (folder / "old_tool.py").exists() and (folder / ao.MANIFEST).exists()
    assert ao.main(["--restore", str(folder)]) == 0
    assert "restored 2 of 2" in capsys.readouterr().out
    assert (scripts / "old_tool.py").read_text(encoding="utf-8") == "print('old_tool.py')"
    assert (scripts / "test_old_tool.py").exists()


def test_restore_never_overwrites_a_file_that_exists_again(tmp_path, monkeypatch, capsys):
    scripts, folder = tmp_path / "scripts", tmp_path / "arch"
    scripts.mkdir(), folder.mkdir()
    (folder / "a.py").write_text("old copy", encoding="utf-8")
    (scripts / "a.py").write_text("newer copy", encoding="utf-8")
    (folder / ao.MANIFEST).write_text(json.dumps({"files": ["a.py"]}), encoding="utf-8")
    monkeypatch.setattr(ao, "HERE", str(scripts))
    assert ao.main(["--restore", str(folder)]) == 0
    assert "restored 0 of 1" in capsys.readouterr().out
    assert (scripts / "a.py").read_text(encoding="utf-8") == "newer copy"


def test_the_manifest_itself_does_not_count_as_a_config_naming_the_script(tmp_path, monkeypatch):
    """Found 03:25 by dry-running it: every orphan looked protected because harness_manifest.json lists them all by name."""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "harness_manifest.json").write_text('{"classes": {"orphan": ["lonely.py"]}}', encoding="utf-8")
    (logs / "real_config.json").write_text('{"task": "used.py"}', encoding="utf-8")
    monkeypatch.setattr(ao, "LOGS", str(logs))
    assert ao._protected(["lonely.py", "used.py"]) == {"used.py"}


def test_retire_only_keeps_just_the_files_the_table_marked_retire():
    disp = {"a.py": "RETIRE", "b.py": "KEEP", "c.py": "MERGE-INTO:x.py", "d.py": "REWRITE-AS-SENSOR", "e.py": "RETIRE"}
    assert ao.retire_only(["e.py", "a.py", "b.py", "c.py", "d.py", "unknown.py"], disp) == ["a.py", "e.py"]
    assert ao.retire_only([], disp) == [] and ao.retire_only(["a.py"], {}) == []
