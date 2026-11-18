# -*- coding: utf-8 -*-
"""Tests for the people sensor: the pure core on invented rows, and main() on a temp logs folder (the real logs are never written)."""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sense_people as sp  # noqa: E402
import intent_store  # noqa: E402

THE_OWNER = 5551234567890123456
RILEY = 792400891202371644
NOW = datetime.datetime(2026, 10, 30, 12, 0, tzinfo=datetime.timezone.utc).timestamp()


def at(minutes_ago):
    """An ISO UTC timestamp the given number of minutes before NOW, in the same shape the daemon logs."""
    return datetime.datetime.fromtimestamp(NOW - minutes_ago * 60, tz=datetime.timezone.utc).isoformat()


def local(epoch):
    return sp._local(epoch)


def dm(mid=1, ago=50, text="are you free tonight", author=RILEY, name="Riley", source="dm_friend", channel=555, attachments=None, reply_to=None, **extra):
    row = {"id": mid, "date": at(ago), "text": text, "attachments": attachments or [], "reply_to": reply_to,
           "author_id": author, "author_name": name, "source": source, "channel_id": channel}
    row.update(extra)
    return row


def sent_row(mid=100, ago=10, reply_to=None, channel_id=None, text="yep"):
    return {"id": mid, "date": at(ago), "text": text, "photo": None, "reply_to": reply_to, "channel_id": channel_id}


def test_a_friend_dm_with_no_reply_after_45_minutes_is_raised_as_tier_two_value_three():
    got = sp.unanswered([dm(mid=1, ago=50)], [], NOW)
    assert len(got) == 1
    what, value, dedupe, not_before, deadline = got[0]
    assert "DM from Riley" in what and "no reply after 45 min" in what and "are you free tonight" in what
    assert value == 3 and dedupe == "dm-unanswered-1"
    assert not_before == local(NOW - 50 * 60 + 45 * 60) and deadline is None


def test_the_grace_period_is_45_minutes_and_the_edge_counts_as_due():
    assert sp.unanswered([dm(ago=44)], [], NOW) == []
    assert len(sp.unanswered([dm(ago=45)], [], NOW)) == 1


def test_a_dm_older_than_24_hours_is_ignored():
    assert sp.unanswered([dm(ago=24 * 60)], [], NOW) != []      # exactly 24 h is still inside the window
    assert sp.unanswered([dm(ago=24 * 60 + 1)], [], NOW) == []


def test_the_owners_own_messages_are_never_raised(monkeypatch):
    monkeypatch.setattr(sp, "THE_OWNER_ID", THE_OWNER)
    assert sp.unanswered([dm(author=THE_OWNER, name="Owner", source="dm", channel=None)], [], NOW) == []


def test_guild_channel_and_group_messages_are_never_raised():
    assert sp.unanswered([dm(source="guild", channel=1525139503893708904)], [], NOW) == []


def test_a_threaded_reply_to_that_message_counts_as_answered():
    got = sp.unanswered([dm(mid=7, ago=60)], [sent_row(mid=8, ago=10, reply_to=7)], NOW)
    assert got == []


def test_a_reply_to_a_different_message_does_not_count():
    assert len(sp.unanswered([dm(mid=7, ago=60)], [sent_row(mid=8, ago=10, reply_to=999)], NOW)) == 1


def test_a_send_in_the_same_dm_channel_after_the_message_counts_as_answered():
    assert sp.unanswered([dm(mid=7, ago=60, channel=555)], [sent_row(mid=8, ago=10, channel_id=555)], NOW) == []


def test_a_send_in_the_same_channel_before_the_message_does_not_count():
    got = sp.unanswered([dm(mid=7, ago=50, channel=555)], [sent_row(mid=8, ago=55, channel_id=555)], NOW)
    assert len(got) == 1


def test_a_send_to_another_channel_does_not_count():
    assert len(sp.unanswered([dm(mid=7, ago=60, channel=555)], [sent_row(mid=8, ago=10, channel_id=777)], NOW)) == 1


def test_bot_and_empty_system_messages_are_skipped_but_an_attachment_only_message_is_kept():
    assert sp.unanswered([dm(text="", attachments=[])], [], NOW) == []           # system message: nothing said, nothing attached
    assert sp.unanswered([dm(author_bot=True)], [], NOW) == []                   # if the daemon ever logs the bot flag
    got = sp.unanswered([dm(text="", attachments=["discord_media/a.png"])], [], NOW)
    assert len(got) == 1 and "(attachment only)" in got[0][0]


def test_friend_text_is_flattened_and_cut_to_60_characters():
    long_text = "line one\nline two " + "x" * 200
    what = sp.unanswered([dm(text=long_text)], [], NOW)[0][0]
    assert "\n" not in what
    body = what.split('"')[1]
    assert body == "line one line two " + "x" * 42 + "..."   # the first 60 characters of the flattened text, then the marker


def test_the_sender_name_is_used_and_nothing_else_of_the_sender_is_quoted():
    what = sp.unanswered([dm(name="Javan Ruiz", author=735411136001998980)], [], NOW)[0][0]
    assert "DM from Javan Ruiz" in what and "735411136001998980" not in what


def test_bad_rows_are_skipped_without_crashing():
    rows = [None, "x", {}, dm(mid=None), {"source": "dm_friend", "author_id": RILEY, "date": "not a date", "id": 3, "text": "hi"}, dm(mid=9, ago=50)]
    got = sp.unanswered(rows, [None, "x", {"reply_to": None, "date": "bad"}], NOW)
    assert [g[2] for g in got] == ["dm-unanswered-9"]


def test_results_come_back_oldest_first_and_each_message_has_its_own_dedupe():
    got = sp.unanswered([dm(mid=2, ago=50), dm(mid=1, ago=70)], [], NOW)
    assert [g[2] for g in got] == ["dm-unanswered-1", "dm-unanswered-2"]


def test_dry_run_prints_what_it_would_raise_writes_nothing_and_ignores_the_kill_switch(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sp, "LOGS", str(tmp_path))
    monkeypatch.setattr(sp, "_now", lambda: NOW)
    (tmp_path / sp.OFF).write_text("off", encoding="utf-8")
    _write(tmp_path, sp.INCOMING, [dm(mid=5, ago=50)])
    before = sorted(os.listdir(str(tmp_path)))
    assert sp.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "would raise tier2 v3 dm_unanswered DM from Riley" in out and "1 finding(s), nothing written" in out
    assert sorted(os.listdir(str(tmp_path))) == before


def test_main_raises_one_tier_two_intent_and_one_event_then_never_again_for_that_message(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sp, "LOGS", str(tmp_path))
    monkeypatch.setattr(sp, "_now", lambda: NOW)
    _write(tmp_path, sp.INCOMING, [dm(mid=5, ago=50)])
    assert sp.main([]) == 0
    assert "1 finding(s), 1 new intent(s)" in capsys.readouterr().out
    intents = intent_store.read_jsonl(intent_store.INTENTS, str(tmp_path))
    assert len(intents) == 1
    assert intents[0]["kind"] == "people_dm_unanswered" and intents[0]["tier"] == 2 and intents[0]["status"] == "open"
    assert intents[0]["dedupe"] == "dm-unanswered-5" and intents[0]["value"] == 3
    assert len(intent_store.read_jsonl(intent_store.EVENTS, str(tmp_path))) == 1
    assert sp.main([]) == 0
    assert "0 new intent(s)" in capsys.readouterr().out
    # closing the intent, even long after the cooldown, must not bring the same message back
    intents[0]["status"] = "dropped"
    intents[0]["closed_epoch"] = NOW - 7 * 3600
    intent_store.write_intents(intents, str(tmp_path))
    assert sp.main([]) == 0
    assert "0 new intent(s)" in capsys.readouterr().out
    assert len(intent_store.read_jsonl(intent_store.INTENTS, str(tmp_path))) == 1


def test_a_reply_that_lands_before_the_run_means_no_intent(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sp, "LOGS", str(tmp_path))
    monkeypatch.setattr(sp, "_now", lambda: NOW)
    _write(tmp_path, sp.INCOMING, [dm(mid=5, ago=50)])
    _write(tmp_path, sp.SENT, [sent_row(mid=6, ago=20, reply_to=5)])
    assert sp.main([]) == 0
    assert "0 finding(s), 0 new intent(s)" in capsys.readouterr().out
    assert intent_store.read_jsonl(intent_store.INTENTS, str(tmp_path)) == []


def test_kill_switch_stops_a_real_run_quietly(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sp, "LOGS", str(tmp_path))
    monkeypatch.setattr(sp, "_now", lambda: NOW)
    (tmp_path / sp.OFF).write_text("off", encoding="utf-8")
    _write(tmp_path, sp.INCOMING, [dm(mid=5, ago=50)])
    assert sp.main([]) == 0 and "off" in capsys.readouterr().out
    assert intent_store.read_jsonl(intent_store.INTENTS, str(tmp_path)) == []


def _write(folder, name, rows):
    with open(os.path.join(str(folder), name), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
