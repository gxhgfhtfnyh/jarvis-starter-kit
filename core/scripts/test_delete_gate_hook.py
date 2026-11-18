# -*- coding: utf-8 -*-
"""Tests for delete_gate_hook.py's pure core. v1 scope only (see the module docstring for what is
NOT covered yet) -- this is the first pass, not the multi-round red-team send_gate_hook.py got."""
import time

import delete_gate_hook as dg


def _bash(cmd):
    return "Bash", {"command": cmd}


class TestRecursiveForceDeletesAreFlagged:
    def test_rm_rf_combined_flag(self):
        c = dg.classify(*_bash("rm -rf /home/user/project"))
        assert c["destructive"] and c["kind"] == "rm_rf"

    def test_rm_with_separate_flags_either_order(self):
        assert dg.classify(*_bash("rm -r -f ./build"))["destructive"]
        assert dg.classify(*_bash("rm -f -r ./build"))["destructive"]

    def test_rm_long_form_flags(self):
        assert dg.classify(*_bash("rm --recursive --force ./build"))["destructive"]

    def test_rm_without_force_is_not_flagged(self):
        assert not dg.classify(*_bash("rm -r ./build"))["destructive"]

    def test_rm_single_file_is_not_flagged(self):
        assert not dg.classify(*_bash("rm notes.md"))["destructive"]

    def test_powershell_remove_item_recurse_force(self):
        c = dg.classify(*_bash('Remove-Item -Recurse -Force "C:\\Users\\x\\vault"'))
        assert c["destructive"] and c["kind"] == "powershell_remove"

    def test_powershell_remove_item_abbreviated_flags(self):
        assert dg.classify(*_bash("Remove-Item -r -fo C:\\temp\\x"))["destructive"]

    def test_powershell_remove_item_recurse_only_not_flagged(self):
        assert not dg.classify(*_bash("Remove-Item -Recurse C:\\temp\\x"))["destructive"]

    def test_del_s_q(self):
        c = dg.classify(*_bash("del /s /q C:\\temp\\old"))
        assert c["destructive"] and c["kind"] == "del_sq"

    def test_del_without_q_not_flagged(self):
        assert not dg.classify(*_bash("del /s C:\\temp\\old"))["destructive"]

    def test_rd_s_q(self):
        c = dg.classify(*_bash("rd /s /q C:\\temp\\old"))
        assert c["destructive"] and c["kind"] == "rd_sq"

    def test_rmdir_s_q(self):
        assert dg.classify(*_bash("rmdir /q /s C:\\temp\\old"))["destructive"]


class TestOtherBulkPatterns:
    def test_find_delete(self):
        c = dg.classify(*_bash("find . -name '*.tmp' -delete"))
        assert c["destructive"] and c["kind"] == "find_delete"

    def test_find_without_delete_not_flagged(self):
        assert not dg.classify(*_bash("find . -name '*.tmp'"))["destructive"]

    def test_git_clean_force(self):
        c = dg.classify(*_bash("git clean -fdx"))
        assert c["destructive"] and c["kind"] == "git_clean"

    def test_git_clean_dry_run_not_flagged(self):
        assert not dg.classify(*_bash("git clean -ndx"))["destructive"]

    def test_git_status_not_flagged(self):
        assert not dg.classify(*_bash("git status"))["destructive"]

    def test_powershell_pipeline_gci_into_remove_item(self):
        c = dg.classify(*_bash("Get-ChildItem C:\\vault | Remove-Item"))
        assert c["destructive"] and c["kind"] == "powershell_pipeline_delete"

    def test_powershell_pipeline_gci_foreach_remove_item(self):
        assert dg.classify(*_bash("Get-ChildItem C:\\vault -Recurse | ForEach-Object { Remove-Item $_.FullName }"))["destructive"]

    def test_powershell_pipeline_gci_into_clear_content(self):
        assert dg.classify(*_bash("Get-ChildItem C:\\vault | % { Clear-Content $_ }"))["destructive"]

    def test_plain_gci_listing_not_flagged(self):
        assert not dg.classify(*_bash("Get-ChildItem C:\\vault"))["destructive"]

    def test_shutil_rmtree(self):
        c = dg.classify(*_bash("python -c \"import shutil; shutil.rmtree('C:/Users/x/vault')\""))
        assert c["destructive"] and c["kind"] == "shutil_rmtree"

    def test_shutil_rmtree_unreadable_target_still_flagged(self):
        c = dg.classify(*_bash("python -c \"import shutil; shutil.rmtree(target_var)\""))
        assert c["destructive"] and c["kind"] == "shutil_rmtree"

    def test_os_remove_loop(self):
        code = "python <<'EOF'\nfor f in glob.glob('notes/*.md'):\n    os.remove(f)\nEOF"
        assert dg.classify(*_bash(code))["destructive"]

    def test_single_os_remove_not_flagged(self):
        assert not dg.classify(*_bash("python -c \"import os; os.remove('a.txt')\""))["destructive"]

    def test_sql_drop_table(self):
        c = dg.classify(*_bash("sqlite3 app.db \"DROP TABLE users;\""))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_sql_truncate_table(self):
        assert dg.classify(*_bash("psql -c 'TRUNCATE TABLE sessions;'"))["destructive"]

    def test_sql_select_not_flagged(self):
        assert not dg.classify(*_bash("sqlite3 app.db \"SELECT * FROM users;\""))["destructive"]

    def test_windows_format_drive(self):
        c = dg.classify(*_bash("format D: /q"))
        assert c["destructive"] and c["kind"] == "drive_format"

    def test_linux_mkfs(self):
        assert dg.classify(*_bash("mkfs.ext4 /dev/sdb1"))["destructive"]

    def test_diskpart_with_clean(self):
        c = dg.classify(*_bash("diskpart /s wipe_script.txt\n# select disk 1\n# clean"))
        assert c["destructive"] and c["kind"] == "diskpart"

    def test_bare_diskpart_listing_not_flagged_without_clean_or_script(self):
        assert not dg.classify(*_bash("diskpart /?"))["destructive"]


class TestStandingExemptions:
    def test_scratchpad_delete_is_exempt(self):
        cmd = r'Remove-Item -Recurse -Force "C:\Users\someone\AppData\Local\Temp\claude\abc\scratch"'
        assert not dg.classify(*_bash(cmd))["destructive"]

    def test_scratchpad_delete_unix_style_is_exempt(self):
        assert not dg.classify(*_bash("rm -rf /tmp/claude/run123/out"))["destructive"]

    def test_tearing_down_a_temp_clone_the_same_command_made_is_exempt(self):
        cmd = 'git clone https://example.com/repo.git /tmp/e2e_check && echo done; Remove-Item -Recurse -Force /tmp/e2e_check'
        assert not dg.classify(*_bash(cmd))["destructive"]

    def test_a_delete_of_something_not_created_in_the_same_command_is_not_exempt(self):
        cmd = 'Remove-Item -Recurse -Force /home/user/notes'
        assert dg.classify(*_bash(cmd))["destructive"]

    def test_edit_tool_on_an_ordinary_file_is_never_flagged(self):
        assert not dg.classify("Edit", {"file_path": "notes.md"})["destructive"]

    def test_write_tool_on_an_ordinary_file_is_never_flagged(self):
        assert not dg.classify("Write", {"file_path": "notes.md", "content": "hi"})["destructive"]


class TestTamperProtection:
    def test_deleting_the_gate_log_itself_is_flagged(self):
        c = dg.classify(*_bash("rm logs/delete_gate.jsonl"))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_editing_the_approvals_file_via_the_edit_tool_is_flagged(self):
        c = dg.classify("Edit", {"file_path": "logs/delete_approvals.jsonl"})
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_reading_the_gate_log_is_not_tamper(self):
        assert not dg.classify(*_bash("cat logs/delete_gate.jsonl"))["destructive"]

    def test_echo_redirect_with_no_recognized_writer_verb_is_still_tamper(self):
        """security review 2026-11-06: a bare redirect, not a known WRITER_CMD, must still be caught."""
        c = dg.classify(*_bash('echo fake >> logs/delete_gate.jsonl'))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_python_write_to_an_approvals_file_is_tamper(self):
        c = dg.classify(*_bash("python -c \"open('logs/delete_approvals.jsonl','a').write('{}')\""))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_forged_approval_row_written_straight_into_the_incoming_log_is_tamper(self):
        """security review 2026-11-06: discord_incoming.jsonl is the approval-evidence source; a
        forged 'yes' row written straight into it would self-approve a deletion if not protected."""
        cmd = 'echo \'{"id":1,"author_id":5551234567890123456,"source":"dm","text":"yes"}\' >> logs/discord_incoming.jsonl'
        c = dg.classify(*_bash(cmd))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_editing_discord_incoming_via_the_edit_tool_is_tamper(self):
        c = dg.classify("Edit", {"file_path": "logs/discord_incoming.jsonl"})
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_reading_discord_incoming_is_not_tamper(self):
        assert not dg.classify(*_bash("grep yes logs/discord_incoming.jsonl"))["destructive"]


class TestFalsePositivesInPlainTextArguments:
    """A mention of a destructive-sounding phrase inside TEXT DATA (an echoed warning, a commit
    message) must not trip the whole-command patterns -- only real code/commands should."""

    def test_drop_table_mentioned_in_an_echoed_warning_is_not_flagged(self):
        assert not dg.classify(*_bash('echo "please dont DROP TABLE in prod"'))["destructive"]

    def test_rm_rf_mentioned_in_a_commit_message_is_not_flagged(self):
        assert not dg.classify(*_bash('git commit -m "docs: warn against rm -rf in scripts"'))["destructive"]

    def test_format_mentioned_in_an_echoed_note_is_not_flagged(self):
        assert not dg.classify(*_bash('echo "do not format D: ever"'))["destructive"]

    def test_a_real_drop_table_through_sqlite_is_still_flagged(self):
        """the masking must not blind the gate to the real thing -- only text-command arguments are blanked."""
        c = dg.classify(*_bash('sqlite3 app.db "DROP TABLE users;"'))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_echoed_text_actually_piped_into_something_that_runs_it_is_still_flagged(self):
        """security review 2026-11-06 round 2: masking must not blind the gate when the echoed text
        is actually CONSUMED (piped into sqlite3), not just printed to the screen."""
        c = dg.classify(*_bash('echo "DROP TABLE users;" | sqlite3 app.db'))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_echoed_text_redirected_into_a_file_that_would_be_run_is_still_flagged(self):
        c = dg.classify(*_bash('echo "format D: /q" > run_me.bat'))
        assert c["destructive"] and c["kind"] == "drive_format"

    def test_safe_text_wrapped_in_command_substitution_is_still_flagged(self):
        """security review 2026-11-06 round 3: $(echo "...") actually SUBSTITUTES/RUNS that text."""
        c = dg.classify(*_bash('x=$(echo "DROP TABLE users;")'))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_safe_text_passed_to_eval_is_still_flagged(self):
        c = dg.classify(*_bash('eval "echo \'format D: /q\'"'))
        assert c["destructive"] and c["kind"] == "drive_format"

    def test_safe_text_passed_to_bash_dash_c_is_still_flagged(self):
        c = dg.classify(*_bash('bash -c "echo \'DROP TABLE users;\'"'))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_a_plain_commit_message_with_no_exec_context_marker_is_still_not_flagged(self):
        """the fail-closed fallback must not swallow the round-2 fix for the ordinary case."""
        assert not dg.classify(*_bash('git commit -m "docs: warn against rm -rf in scripts"'))["destructive"]

    def test_bare_powershell_grouping_parens_is_still_flagged(self):
        """security review 2026-11-06 round 4: a bare (...) grouping/subexpression has no eval/-c/iex
        marker at all, so the round-3 marker list would have missed this."""
        c = dg.classify(*_bash('(Write-Output "DROP TABLE users;")'))
        assert c["destructive"] and c["kind"] == "sql_drop"

    def test_powershell_encodedcommand_is_still_flagged(self):
        c = dg.classify(*_bash("powershell -EncodedCommand \"echo 'format D: /q'\""))
        assert c["destructive"] and c["kind"] == "drive_format"


class TestTamperViaLogsWildcardOrVariable:
    """security review 2026-11-06 round 2: RX_TAMPER only matches the exact literal filenames: a
    wildcard or a variable reaching into the logs directory needs its own, broader check."""

    def test_deleting_the_logs_directory_by_wildcard_is_tamper(self):
        c = dg.classify(*_bash("rm -rf logs/delete_*"))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_redirecting_into_a_variable_pointing_at_the_logs_dir_is_tamper(self):
        c = dg.classify(*_bash('echo fake >> $LOGS_DIR/discord_incoming.jsonl'))
        assert c["destructive"] and c["kind"] == "gate_tamper"

    def test_an_unrelated_wildcard_delete_with_no_logs_reference_is_not_tamper(self):
        c = dg.classify(*_bash("rm -rf build/output_*"))
        assert c["kind"] != "gate_tamper"

    def test_reading_a_logs_wildcard_is_not_tamper(self):
        assert not dg.classify(*_bash("cat logs/delete_*.jsonl"))["destructive"]


class TestRmtreeAndRemoveLoopFallbackScan:
    """security review 2026-11-06 round 2: detection must not depend on _python_blocks() recognizing
    a formal heredoc/-c block -- a whole-text fallback must catch code reached another way."""

    def test_rmtree_fed_through_a_printf_pipe_into_python_is_flagged(self):
        c = dg.classify(*_bash("printf 'import shutil\\nshutil.rmtree(\"x\")\\n' | python"))
        assert c["destructive"] and c["kind"] == "shutil_rmtree"

    def test_remove_loop_fed_through_a_cat_pipe_into_python_is_flagged(self):
        """not a formal `python <<EOF` block (_python_blocks wouldn't recognize this shape), so this
        only passes once the whole-text fallback scan is real."""
        cmd = "cat <<'EOF' | python\nfor f in glob.glob('x'):\n    os.remove(f)\nEOF"
        assert dg.classify(*_bash(cmd))["destructive"]


class TestContentHash:
    def test_same_whitespace_normalised_text_hashes_the_same(self):
        assert dg.content_hash("rm   -rf   x") == dg.content_hash("rm -rf x")

    def test_different_text_hashes_differently(self):
        assert dg.content_hash("rm -rf x") != dg.content_hash("rm -rf y")


class TestValidApprovals:
    def test_a_fresh_approval_is_valid(self):
        now = time.time()
        rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "hash": "abc123"}]
        assert "abc123" in dg.valid_approvals(rows, [], now)

    def test_an_approval_older_than_30_minutes_is_not_valid(self):
        now = time.time()
        old = now - 31 * 60
        rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(old)), "hash": "abc123"}]
        assert "abc123" not in dg.valid_approvals(rows, [], now)

    def test_a_used_approval_is_not_valid_again(self):
        now = time.time()
        rows = [{"ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "hash": "abc123"}]
        used = [{"hash": "abc123", "t": now}]
        assert "abc123" not in dg.valid_approvals(rows, used, now)


class TestDecide:
    def test_non_destructive_always_allows(self):
        assert dg.decide({"destructive": False}, set(), "enforce", "h1") == ("allow", "")

    def test_destructive_with_a_matching_approval_is_approved(self):
        decision, _ = dg.decide({"destructive": True, "why": "x"}, {"h1"}, "enforce", "h1")
        assert decision == "approved"

    def test_destructive_without_approval_in_shadow_mode_would_block_but_does_not_block(self):
        decision, _ = dg.decide({"destructive": True, "why": "x"}, set(), "shadow", "h1")
        assert decision == "would_block"

    def test_destructive_without_approval_in_enforce_mode_blocks(self):
        decision, _ = dg.decide({"destructive": True, "why": "x"}, set(), "enforce", "h1")
        assert decision == "block"


class TestApprovalEvidence:
    def test_a_real_yes_after_the_hold_is_evidence(self, monkeypatch):
        monkeypatch.setattr(dg, "THE_OWNER_ID", 5551234567890123456)
        dms = [{"author_id": dg.THE_OWNER_ID, "source": "dm", "date": "2026-11-06T11:10:00", "text": "yes", "id": 1}]
        since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
        now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
        ev = dg.approval_evidence(dms, since, now)
        assert ev is not None and ev["id"] == 1

    def test_a_yes_before_the_hold_does_not_count(self, monkeypatch):
        monkeypatch.setattr(dg, "THE_OWNER_ID", 5551234567890123456)
        dms = [{"author_id": dg.THE_OWNER_ID, "source": "dm", "date": "2026-11-06T10:00:00", "text": "yes", "id": 1}]
        since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
        now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
        assert dg.approval_evidence(dms, since, now) is None

    def test_a_question_is_not_a_yes(self, monkeypatch):
        monkeypatch.setattr(dg, "THE_OWNER_ID", 5551234567890123456)
        dms = [{"author_id": dg.THE_OWNER_ID, "source": "dm", "date": "2026-11-06T11:10:00", "text": "are you sure?", "id": 1}]
        since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
        now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
        assert dg.approval_evidence(dms, since, now) is None

    def test_someone_elses_yes_does_not_count(self):
        dms = [{"author_id": 12345, "source": "dm", "date": "2026-11-06T11:10:00", "text": "yes", "id": 1}]
        since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
        now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
        assert dg.approval_evidence(dms, since, now) is None


class TestRunEndToEnd:
    def test_shadow_mode_never_blocks_even_a_destructive_call(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("shadow", encoding="utf-8")
        code, _ = dg.run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}, logs=str(tmp_path))
        assert code == 0
        assert (tmp_path / "delete_gate.jsonl").exists()

    def test_enforce_mode_blocks_an_unapproved_destructive_call(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("enforce", encoding="utf-8")
        code, msg = dg.run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}, logs=str(tmp_path))
        assert code == 2 and "delete_gate" in msg

    def test_enforce_mode_allows_a_safe_call(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("enforce", encoding="utf-8")
        code, _ = dg.run({"tool_name": "Bash", "tool_input": {"command": "ls -la"}}, logs=str(tmp_path))
        assert code == 0

    def test_kill_switch_allows_everything_and_logs_nothing(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("enforce", encoding="utf-8")
        (tmp_path / "delete_gate_off.txt").write_text("", encoding="utf-8")
        code, _ = dg.run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}, logs=str(tmp_path))
        assert code == 0
        assert not (tmp_path / "delete_gate.jsonl").exists()

    def test_kill_switch_is_ignored_in_an_unattended_run(self, tmp_path, monkeypatch):
        """security review 2026-11-06: a run nobody is watching must never honor a kill-switch file
        an agent (or a planted script) could leave behind -- mirrors send_gate_hook.py's own rule."""
        monkeypatch.setenv("JARVIS_UNATTENDED", "1")
        (tmp_path / "delete_gate_off.txt").write_text("", encoding="utf-8")
        code, _ = dg.run({"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}, logs=str(tmp_path))
        assert code == 2
        assert (tmp_path / "delete_gate.jsonl").exists()

    def test_approving_then_rerunning_the_exact_text_allows_it(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("enforce", encoding="utf-8")
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}
        code1, _ = dg.run(payload, logs=str(tmp_path))
        assert code1 == 2
        dg.approve("rm -rf /home/user/stuff", "the owner", logs=str(tmp_path))
        code2, _ = dg.run(payload, logs=str(tmp_path))
        assert code2 == 0

    def test_one_approval_does_not_cover_a_second_call(self, tmp_path):
        (tmp_path / "delete_gate_mode.txt").write_text("enforce", encoding="utf-8")
        payload = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /home/user/stuff"}}
        dg.approve("rm -rf /home/user/stuff", "the owner", logs=str(tmp_path))
        code1, _ = dg.run(payload, logs=str(tmp_path))
        assert code1 == 0
        code2, _ = dg.run(payload, logs=str(tmp_path))
        assert code2 == 2


class TestMain:
    def test_report_subcommand_runs_cleanly(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(dg, "LOGS", str(tmp_path))
        assert dg.main(["report"]) == 0
        assert "row(s) total" in capsys.readouterr().out

    def test_cli_approve_without_text_fails_cleanly(self, capsys):
        assert dg._cli_approve(["approve"]) == 2

    def test_unattended_blocks_cli_approval(self, monkeypatch):
        monkeypatch.setenv("JARVIS_UNATTENDED", "1")
        assert dg.main(["approve", "--text", "rm -rf x"]) == 2
