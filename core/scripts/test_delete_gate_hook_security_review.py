# -*- coding: utf-8 -*-
"""Security review (2026-11-18) findings on delete_gate_hook.py, each with a real test:
1. WRITER_CMDS missed common PowerShell short aliases and plain creation verbs, letting a command tamper
   with the kill-switch/approval files without matching any recognized writer verb.
2. classify() already treats a direct Write/Edit/NotebookEdit onto a protected file as gate_tamper, but the
   documented hook registration only covered Bash|PowerShell, so that branch never actually ran.
3. The scratch-path exemption matched the raw command text, so a path escaping the scratch directory via
   `..` segments was still treated as "scratch" and never flagged as destructive.
4. A generic "yes" DM was bound only by arrival time, not by content, so an unrelated yes-shaped reply
   could be spent approving an unrelated held destructive command; also never refused when the owner id
   isn't configured yet."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import delete_gate_hook as dg  # noqa: E402


def test_touch_on_the_kill_switch_file_is_tamper():
    assert dg._is_tamper(r"touch logs/delete_gate_off.txt") is True


def test_powershell_short_aliases_are_recognized_as_writers():
    assert dg._is_tamper(r"ni logs/delete_gate_off.txt") is True
    assert dg._is_tamper(r"sc logs/delete_gate_mode.txt enforce") is True
    assert dg._is_tamper(r"ac logs/discord_incoming.jsonl -value x") is True


def test_a_command_word_no_allowlist_has_ever_named_is_tamper_too():
    """Round 2 (2026-11-18): the fix must not be another allowlist a reviewer can keep finding gaps in --
    a command word WRITER_CMDS has never heard of must still be refused, since the check now denies by
    default rather than only recognizing known writers."""
    assert dg._is_tamper(r"some-brand-new-tool-nobody-listed logs/delete_gate_off.txt") is True


def test_a_genuinely_read_only_command_on_a_protected_file_is_not_tamper():
    assert dg._is_tamper(r"cat logs/delete_gate_off.txt") is False
    assert dg._is_tamper(r"grep enforce logs/delete_gate_mode.txt") is False


def test_registration_docstring_now_covers_write_edit_notebookedit():
    assert "Write|Edit|NotebookEdit" in dg.__doc__


def test_a_scratch_path_that_escapes_via_dotdot_is_not_exempt():
    escaping = r"C:\Users\someone\AppData\Local\Temp\claude\..\..\..\Jarvis2\logs\delete_gate_off.txt"
    assert dg._is_scratch_path(escaping) is False


def test_a_genuine_scratch_path_is_still_exempt():
    genuine = r"C:\Users\someone\AppData\Local\Temp\claude\abc123\scratch.txt"
    assert dg._is_scratch_path(genuine) is True


def test_approval_evidence_requires_the_exact_code_when_one_is_given(monkeypatch):
    monkeypatch.setattr(dg, "THE_OWNER_ID", 5551234567890123456)
    since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
    now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
    wrong_code_dm = [{"author_id": dg.THE_OWNER_ID, "source": "dm", "date": "2026-11-06T11:10:00", "text": "yes", "id": 1}]
    assert dg.approval_evidence(wrong_code_dm, since, now, code="abc123") is None
    right_code_dm = [{"author_id": dg.THE_OWNER_ID, "source": "dm", "date": "2026-11-06T11:10:00", "text": "abc123", "id": 1}]
    ev = dg.approval_evidence(right_code_dm, since, now, code="abc123")
    assert ev is not None and ev["id"] == 1


def test_approval_evidence_never_approves_when_owner_not_configured(monkeypatch):
    monkeypatch.setattr(dg, "THE_OWNER_ID", None)
    since = dg.dt.datetime(2026, 11, 6, 11, 0, 0).timestamp()
    now = dg.dt.datetime(2026, 11, 6, 11, 20, 0).timestamp()
    dms = [{"author_id": None, "source": "dm", "date": "2026-11-06T11:10:00", "text": "yes", "id": 1}]
    assert dg.approval_evidence(dms, since, now) is None


def test_short_code_matches_send_gate_hooks_own_scheme():
    assert dg.short_code("abcdef1234567890") == "abcdef"
    assert len(dg.short_code("abcdef1234567890")) == dg.SHORT_CODE_LEN


def test_cli_approve_refuses_when_two_held_commands_collide_on_the_same_code(tmp_path, monkeypatch):
    """Security review round 2 (2026-11-18): a reply meant to approve one held command must never get spent
    on a different held command that happens to share the same short code."""
    monkeypatch.setattr(dg, "LOGS", str(tmp_path))
    monkeypatch.setattr(dg, "short_code", lambda h: "samecod")  # force a deliberate collision
    ts = "2026-11-18T10:00:00"
    rows = [
        {"ts": ts, "decision": "would_block", "hash": "aaa111", "kind": "rm_rf", "target": "x"},
        {"ts": ts, "decision": "would_block", "hash": "bbb222", "kind": "rm_rf", "target": "y"},
    ]
    with open(os.path.join(str(tmp_path), dg.LOG), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(__import__("json").dumps(r) + "\n")
    rc = dg._cli_approve(["approve-last"], logs=str(tmp_path), now=dg.dt.datetime(2026, 11, 18, 10, 5, 0).timestamp())
    assert rc == 5
