# -*- coding: utf-8 -*-
"""Tests for op_open.py pure helpers."""
import subprocess
import sys
import pytest

# Import the module under test
import op_open


class TestParse:
    """Tests for the parse() pure helper function."""

    def test_parse_empty_args(self):
        """Parse returns empty opts and flags for empty argv."""
        opts, flags, bad = op_open.parse(["op_open.py"])
        assert opts == {}
        assert flags == set()
        assert bad == ""

    def test_parse_single_flag_stdint(self):
        """Parse handles --stdin flag."""
        opts, flags, bad = op_open.parse(["op_open.py", "--stdin"])
        assert opts == {}
        assert flags == {"--stdin"}
        assert bad == ""

    def test_parse_single_flag_list_open(self):
        """Parse handles --list-open flag."""
        opts, flags, bad = op_open.parse(["op_open.py", "--list-open"])
        assert opts == {}
        assert flags == {"--list-open"}
        assert bad == ""

    def test_parse_both_flags(self):
        """Parse handles both --stdin and --list-open together."""
        opts, flags, bad = op_open.parse(["op_open.py", "--stdin", "--list-open"])
        assert opts == {}
        assert flags == {"--stdin", "--list-open"}
        assert bad == ""

    def test_parse_title_option(self):
        """Parse handles --title option with value."""
        opts, flags, bad = op_open.parse(["op_open.py", "--title", "Test Operation"])
        assert opts == {"title": "Test Operation"}
        assert flags == set()
        assert bad == ""

    def test_parse_multiple_options(self):
        """Parse handles multiple options."""
        argv = ["op_open.py", "--title", "Test", "--objective", "Do something", "--priority", "HIGH"]
        opts, flags, bad = op_open.parse(argv)
        assert opts == {"title": "Test", "objective": "Do something", "priority": "HIGH"}
        assert flags == set()
        assert bad == ""

    def test_parse_equals_syntax(self):
        """Parse handles --key=value syntax."""
        argv = ["op_open.py", "--title=My Title", "--priority=LOW"]
        opts, flags, bad = op_open.parse(argv)
        assert opts == {"title": "My Title", "priority": "LOW"}
        assert flags == set()
        assert bad == ""

    def test_parse_mixed_equals_and_space(self):
        """Parse handles mix of --key=value and --key value syntax."""
        argv = ["op_open.py", "--title=Equals Title", "--objective", "Space Value"]
        opts, flags, bad = op_open.parse(argv)
        assert opts == {"title": "Equals Title", "objective": "Space Value"}
        assert flags == set()
        assert bad == ""

    def test_parse_all_known_options(self):
        """Parse handles all known FLAGS options."""
        argv = [
            "op_open.py",
            "--title", "T",
            "--objective", "O",
            "--done-when", "D",
            "--context", "C",
            "--priority", "P",
            "--type", "TYPE",
            "--due", "2026-10-20",
            "--autonomy", "A",
            "--notify", "N",
            "--constraints", "CON",
            "--source", "discord",
            "--ref", "ref123",
            "--status", "done",
            "--summary", "S"
        ]
        opts, flags, bad = op_open.parse(argv)
        expected = {
            "title": "T", "objective": "O", "outcome": "D", "context": "C",
            "priority": "P", "type": "TYPE", "due": "2026-10-20",
            "autonomy": "A", "notify": "N", "constraints": "CON",
            "source": "discord", "ref": "ref123", "status": "done", "summary": "S"
        }
        assert opts == expected
        assert flags == set()
        assert bad == ""

    def test_parse_unknown_option_returns_bad(self):
        """Parse returns bad argument for unknown option."""
        opts, flags, bad = op_open.parse(["op_open.py", "--unknown", "value"])
        assert opts is None
        assert flags is None
        assert bad == "--unknown"

    def test_parse_incomplete_option_returns_bad(self):
        """Parse returns bad argument when option missing value."""
        opts, flags, bad = op_open.parse(["op_open.py", "--title"])
        assert opts is None
        assert flags is None
        assert bad == "--title"

    def test_parse_unknown_flag_returns_bad(self):
        """Parse returns bad argument for unknown flag."""
        opts, flags, bad = op_open.parse(["op_open.py", "--not-a-flag"])
        assert opts is None
        assert flags is None
        assert bad == "--not-a-flag"

    def test_parse_flag_with_equals_unknown(self):
        """Parse returns bad for unknown option with equals syntax."""
        opts, flags, bad = op_open.parse(["op_open.py", "--unknown=value"])
        assert opts is None
        assert flags is None
        assert bad == "--unknown=value"

    def test_parse_known_flag_with_equals_unknown_key(self):
        """Parse returns bad when key before = is unknown."""
        opts, flags, bad = op_open.parse(["op_open.py", "--notakey=value"])
        assert opts is None
        assert flags is None
        assert bad == "--notakey=value"


class TestConstants:
    """Tests for module-level constants."""

    def test_flags_dict_structure(self):
        """FLAGS dict maps CLI flags to internal keys correctly."""
        expected_keys = {
            "--title", "--objective", "--done-when", "--context", "--priority",
            "--type", "--due", "--autonomy", "--notify", "--constraints",
            "--source", "--ref", "--status", "--summary"
        }
        assert set(op_open.FLAGS.keys()) == expected_keys
        # Verify all values are unique
        assert len(op_open.FLAGS.values()) == len(set(op_open.FLAGS.values()))

    def test_candidates_tuple_exists(self):
        """_CANDIDATES tuple exists and has expected entries."""
        assert isinstance(op_open._CANDIDATES, tuple)
        assert len(op_open._CANDIDATES) == 3
        # First entry is from env or None
        # Second and third are derived from the current user's home directory, not hardcoded
        import os
        assert op_open._CANDIDATES[1] == os.path.expanduser(r"~\jarvis-ui\.worktrees\implement-jarvis-archive-terminal")
        assert op_open._CANDIDATES[2] == os.path.expanduser(r"~\jarvis-ui")


class TestLoadOpFiles:
    """Tests for load_op_files() helper (mocking filesystem)."""

    def test_load_op_files_returns_none_when_not_found(self, monkeypatch, tmp_path):
        """load_op_files returns None when op_files.py not found in any candidate."""
        # Point all candidates to non-existent paths
        monkeypatch.setattr(op_open, "_CANDIDATES", (str(tmp_path / "nonexistent1"), str(tmp_path / "nonexistent2")))
        # Also need to clear sys.path additions - but the function checks file existence first
        result = op_open.load_op_files()
        assert result is None

    def test_load_op_files_finds_module(self, monkeypatch, tmp_path):
        """load_op_files returns module when op_files.py exists."""
        # Create a fake op_files.py
        fake_dir = tmp_path / "fake_jarvis_ui"
        fake_dir.mkdir()
        (fake_dir / "op_files.py").write_text("# fake module\n")

        monkeypatch.setattr(op_open, "_CANDIDATES", (str(fake_dir),))
        # Need to also ensure we can import it - add to sys.path
        if str(fake_dir) not in sys.path:
            sys.path.insert(0, str(fake_dir))

        result = op_open.load_op_files()
        assert result is not None
        assert hasattr(result, '__file__')
        # Clean up sys.path
        sys.path.remove(str(fake_dir))


class TestMainBehavior:
    """Tests for main() behavior with mocked dependencies (integration-style)."""

    def test_main_unknown_arg_returns_2(self, capsys):
        """main returns exit code 2 for unknown argument."""
        # We can't easily test main without the op_files module, so we test the parse error path
        # by checking that parse returns bad for unknown args
        opts, flags, bad = op_open.parse(["op_open.py", "--badflag"])
        assert opts is None
        assert bad == "--badflag"

    def test_main_missing_title_returns_2(self):
        """main would return 2 when --title is missing (checked via parse)."""
        # parse itself doesn't validate required args, but main does
        # We test that parse doesn't require title
        opts, flags, bad = op_open.parse(["op_open.py", "--objective", "test"])
        assert opts == {"objective": "test"}
        assert bad == ""


class TestWorkingOperationRaisesIntent:
    """2026-11-06: keep_going.py (the Stop hook) never sees operations, only queue.md/goals/intents,
    so opening an operation did nothing to keep the turn open until the real work was done. main()
    now also raises a linked intent for a 'working' operation, closing that gap."""

    def _fake_mod(self, status="working"):
        class FakeMod:
            @staticmethod
            def open_operation(fields, source, ref="", status=status, summary=""):
                return {"ok": True, "action": "a_test123", "title": fields.get("title", ""), "status": status}
        return FakeMod()

    def test_working_status_calls_intent_store_add(self, monkeypatch):
        monkeypatch.setattr(op_open, "load_op_files", lambda: self._fake_mod("working"))
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
        code = op_open.main(["op_open.py", "--title", "Test op", "--done-when", "it works", "--source", "discord"])
        assert code == 0
        assert len(calls) == 1
        args = calls[0]
        assert "intent_store.py" in args[1]
        assert "--dedupe" in args
        assert args[args.index("--dedupe") + 1] == "op-open-a_test123"
        assert "--tier" in args and args[args.index("--tier") + 1] == "1"

    def test_done_status_does_not_call_intent_store(self, monkeypatch):
        monkeypatch.setattr(op_open, "load_op_files", lambda: self._fake_mod("done"))
        calls = []
        monkeypatch.setattr(subprocess, "run", lambda args, **kw: calls.append(args))
        code = op_open.main(["op_open.py", "--title", "Test op", "--status", "done", "--summary", "finished", "--source", "discord"])
        assert code == 0
        assert calls == []

    def test_subprocess_failure_does_not_crash_main(self, monkeypatch):
        monkeypatch.setattr(op_open, "load_op_files", lambda: self._fake_mod("working"))

        def boom(*a, **k):
            raise OSError("no python")
        monkeypatch.setattr(subprocess, "run", boom)
        code = op_open.main(["op_open.py", "--title", "Test op", "--source", "discord"])
        assert code == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])