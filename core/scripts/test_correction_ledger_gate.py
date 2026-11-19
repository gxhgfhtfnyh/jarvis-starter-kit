"""Tests for correction_ledger_gate's pure core."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import correction_ledger_gate as clg  # noqa: E402


def test_looks_like_correction_matches_real_phrasings():
    assert clg.looks_like_correction("no that's wrong, not what i asked for")
    assert clg.looks_like_correction("you forgot the thing again")
    assert clg.looks_like_correction("why did u do that")


def test_looks_like_correction_false_on_plain_chat():
    assert not clg.looks_like_correction("sounds good, go ahead")
    assert not clg.looks_like_correction("")
    assert not clg.looks_like_correction(None)


def test_ledger_call_in_turn_detects_a_real_add_call():
    calls = [("Bash", {"command": 'python "J:/Jarvis2/scripts/ledger.py" add 123 "a" "b" "c" "d"'})]
    assert clg.ledger_call_in_turn(calls)


def test_ledger_call_in_turn_false_without_one():
    calls = [("Bash", {"command": "echo hi"}), ("Write", {"file_path": "x.py"})]
    assert not clg.ledger_call_in_turn(calls)
    assert not clg.ledger_call_in_turn([])
    assert not clg.ledger_call_in_turn(None)


def _user_row(text, uuid="u1"):
    return {"type": "user", "uuid": uuid, "message": {"content": text}}


def _asst_text_row(text):
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _tool_use_row(name, command):
    return {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": {"command": command}}]}}


def test_find_correction_detects_a_real_correction_with_no_ledger_call():
    rows = [_user_row("that's wrong, you got distracted"), _asst_text_row("ok sorry")]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, had_call = found
    assert msg_id == "u1"
    assert "wrong" in text
    assert had_call is False


def test_find_correction_none_on_plain_chat():
    rows = [_user_row("sounds good"), _asst_text_row("great")]
    assert clg.find_correction(rows) is None


def test_find_correction_sees_a_ledger_call_already_made_this_turn():
    rows = [
        _user_row("that's wrong"),
        _tool_use_row("Bash", 'python "J:/Jarvis2/scripts/ledger.py" add 1 "a" "b" "c" "d"'),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    _msg_id, _text, had_call = found
    assert had_call is True


def test_find_correction_ignores_assistant_only_rows():
    rows = [_asst_text_row("that's wrong on my part")]
    assert clg.find_correction(rows) is None


def test_find_correction_uses_the_most_recent_user_message():
    rows = [_user_row("that's wrong", uuid="u1"), _asst_text_row("ok"), _user_row("sounds good", uuid="u2")]
    assert clg.find_correction(rows) is None  # the LAST user message (u2) is plain chat, not u1's correction


def test_find_correction_skips_a_synthetic_notification_row_and_finds_the_real_message_underneath():
    # 2026-11-12, found live: a <task-notification> block can arrive as its own "user" row after his real message,
    # outranking it as "the last user message" even though he never typed it.
    rows = [
        _user_row("why did u stop?", uuid="real1"),
        _asst_text_row("checking something"),
        _user_row("<task-notification>\n<task-id>x</task-id>\n<summary>Monitor event</summary>\n</task-notification>", uuid="synthetic1"),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, _had_call = found
    assert msg_id == "real1"
    assert "stop" in text


def test_find_correction_none_when_only_a_synthetic_row_exists():
    rows = [_user_row("<system-reminder>some internal note</system-reminder>", uuid="s1")]
    assert clg.find_correction(rows) is None


def test_find_correction_skips_a_skill_reinvocation_body_and_finds_the_real_message_underneath():
    """2026-11-14, found live: a Skill-tool re-invocation injected the research skill's own
    markdown body as a plain user row with no angle-bracket tag -- SYNTHETIC_WRAPPER alone
    missed it, and a stray word inside the skill's own text got read as a live correction."""
    rows = [
        _user_row("that was wrong, not what i asked for", uuid="real1"),
        _asst_text_row("got it"),
        _user_row(
            "(Re-invocation of /research -- the skill instructions were previously loaded...)\n"
            "Base directory for this skill: J:\\Jarvis2\\.claude\\skills\\research\n\n"
            "# Deep Research\nsomething that happens to contain the word mistake in its own text",
            uuid="synthetic1",
        ),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, _had_call = found
    assert msg_id == "real1"
    assert "wrong" in text


def test_synthetic_skill_body_matches_the_real_shape_not_one_enumerated_skill_name():
    assert clg.SYNTHETIC_SKILL_BODY.match("Base directory for this skill: /anywhere/at/all")
    assert clg.SYNTHETIC_SKILL_BODY.match("(Re-invocation of /some-other-skill -- ...)")
    assert not clg.SYNTHETIC_SKILL_BODY.match("that's wrong, not what i asked for")


def test_find_correction_skips_a_skill_body_with_no_preamble_line_at_all():
    """2026-11-15, third instance of the same trap: invoking update-config injected its SKILL.md
    starting directly with a markdown H1 ("# Update Config Skill"), no "Base directory" or
    "Re-invocation of" preamble line at all -- the first two fingerprints both missed it, and the
    skill's own usage-example text ("WRONG (replaces existing permissions)") read as a correction."""
    rows = [
        _user_row("that's wrong, not what i asked for", uuid="real2"),
        _asst_text_row("got it"),
        _user_row(
            "# Update Config Skill\n\nModify Claude Code configuration by updating settings.json "
            "files.\n\n**WRONG** (replaces existing permissions):\n```json\n{ \"permissions\": {} }\n```",
            uuid="synthetic2",
        ),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, _had_call = found
    assert msg_id == "real2"
    assert "wrong" in text


def test_find_correction_skips_a_post_compaction_summary_row():
    # 2026-11-12, found live (second instance of the same bug class as the task-notification one above): a
    # post-compaction summary is injected as its own synthetic "user" row opening with
    # <artifact-content-authored-by-others/>, and its huge recap text can match CORRECT on a stray word inside it.
    summary_text = "<artifact-content-authored-by-others/>\nThe summarized conversation... it was wrong and he forgot the thing."
    rows = [_user_row("why did u stop?", uuid="real1"), _user_row(summary_text, uuid="synthetic2")]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, _had_call = found
    assert msg_id == "real1"
    assert "stop" in text


def test_synthetic_wrapper_matches_any_leading_tag_not_just_known_ones():
    assert clg.SYNTHETIC_WRAPPER.match("<some-future-tag/>\nwhatever")
    assert clg.SYNTHETIC_WRAPPER.match("<task-notification>\n...")
    assert not clg.SYNTHETIC_WRAPPER.match("that's wrong, not what i asked for")


def _inbox_hook_row(msg_id, text):
    """Shapes a row the way inbox_hook.py's real PostToolUse output actually lands in the live
    transcript (type "attachment", not "user"/"assistant" -- see the 2026-11-19 note)."""
    inner = (
        "[NEW DISCORD DM from the owner arrived while you were working; read it now and reply FIRST "
        "before continuing (the watcher may deliver it again; reply only once)]\n"
        "id %s: %s\n[SCOPE LOCK] CURRENT task: see focus.md" % (msg_id, text)
    )
    return {
        "type": "attachment",
        "attachment": {
            "type": "hook_success",
            "stdout": json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": inner}}),
        },
    }


def test_find_correction_catches_a_real_discord_correction_the_old_path_missed():
    """2026-11-19, found live: he had to say "why are u putting it in backlog... thats a bad habit" out loud
    before any fix happened -- the gate never raised an intent on its own all night. Root cause: every Discord
    message this whole Discord-relay-driven session lands as inbox_hook's "attachment" row, never as a plain
    "type": "user" row, so the old type-filtered scan (kept below as the terminal-typed fallback) never even
    saw it."""
    rows = [
        _inbox_hook_row(1000000000000000001, "why are u putting it in backlog vs actually doing it... thats a bad habit"),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, text, had_call = found
    assert msg_id == "1000000000000000001"
    assert "bad habit" in text
    assert had_call is False


def test_find_correction_discord_path_sees_a_ledger_call_already_made_after_it():
    rows = [
        _inbox_hook_row(999, "that's wrong, not what i asked for"),
        _tool_use_row("Bash", 'python "J:/Jarvis2/scripts/ledger.py" add 1 "a" "b" "c" "d"'),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    _msg_id, _text, had_call = found
    assert had_call is True


def test_find_correction_discord_path_ignores_a_ledger_call_before_it():
    """A ledger.py add from an earlier turn, before this message even arrived, must not suppress
    this message's own intent."""
    rows = [
        _tool_use_row("Bash", 'python "J:/Jarvis2/scripts/ledger.py" add 1 "a" "b" "c" "d"'),
        _inbox_hook_row(888, "that's wrong, not what i asked for"),
    ]
    found = clg.find_correction(rows)
    assert found is not None
    _msg_id, _text, had_call = found
    assert had_call is False


def test_find_correction_discord_path_uses_the_last_of_several_echoed_messages():
    """inbox_hook.py re-echoes the same block on every subsequent tool call within a turn until the next
    real message arrives; the gate must land on the truly most recent id, not an earlier echo or an
    earlier distinct message."""
    rows = [
        _inbox_hook_row(1, "sounds good"),
        _inbox_hook_row(1, "sounds good"),  # re-echoed by a later hook fire, same message
        _inbox_hook_row(2, "no that's wrong"),
        _inbox_hook_row(2, "no that's wrong"),  # re-echoed again
    ]
    found = clg.find_correction(rows)
    assert found is not None
    msg_id, _text, _had_call = found
    assert msg_id == "2"


def test_find_correction_discord_path_none_on_plain_chat():
    rows = [_inbox_hook_row(1, "sounds good, go ahead")]
    assert clg.find_correction(rows) is None


def test_find_last_discord_message_ignores_unrelated_id_colon_text_shapes():
    """An "id N: text" shape that appears outside the real inbox_hook header must not be mistaken for one."""
    rows = [{"type": "assistant", "message": {"content": [{"type": "text", "text": "id 5: not a discord dm, just text"}]}}]
    assert clg.find_last_discord_message(rows) is None
