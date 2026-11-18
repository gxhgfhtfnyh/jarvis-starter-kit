# -*- coding: utf-8 -*-
"""Presence from his own words: the detector on real messages he sent (hits and non-hits), the cursor logic, and the presence_auto changes that go with it
(phone messages are not location, the hold after a statement of his is longer than after a hand-made change)."""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import presence_phrases as pp  # noqa: E402
import presence_auto as pa  # noqa: E402

ME = pp.THE_OWNER_ID

REAL_HITS = [
    ("im at sch now lol", "away"), ("ok im heading out byeee", "away"), ("im out for dinner", "away"), ("gm im bouta head to school", "away"),
    ("and im out shouldnt the mode be away", "away"), ("im home", "home"), ("im home lol", "home"), ("jarvis im back", "home"), ("gm", "home"), ("gm :)", "home"),
    ("morning", "home"), ("i just woke up lol", "home"), ("yup im  home and  i fell asleep on the couch for like 2 hrs lol", "home"),
    ("gn", "asleep"), ("ok gn", "asleep"), ("im going to  bed gn", "asleep"), ("ok im going to sleep u test them properly n report when im awake", "asleep"),
    ("yea im bouta go to sleep gn loom", "asleep"), ("im heading to bed lol", "asleep"), ("anyways ill leave you to it Gn <3", "asleep"),
]
REAL_MISSES = [
    "i will see when im home", "that will be for when im home", "keep working till im back", "keep working until im back. add more cool stuff", "ltr when i am home",
    "i will use it when im in school so thats like studying during the day", "im out of game alr", "im out of game", "is the new harness done?", "its a boring ass briefing TvT",
    "how confident r u in the new harness", "what model r u now?", "does the newharness eat more tokens every msg i send", "am i home?", "im not home yet", "he is at school today",
    "maybe fix the presence thingy still cant consistently set the right presence", "thats.... up to u to adapt as we go lol", "",
]


@pytest.mark.parametrize("text,mode", REAL_HITS)
def test_what_he_really_said_is_understood(text, mode):
    assert pp.detect(text) == mode


@pytest.mark.parametrize("text", REAL_MISSES)
def test_plans_questions_negations_and_other_chatter_are_ignored(text):
    assert pp.detect(text) is None


def test_asleep_beats_away_beats_home_when_a_message_says_several_things():
    assert pp.detect("gm im bouta head to school") == "away"
    assert pp.detect("im home gn") == "asleep"


@pytest.mark.parametrize("text,mode", [
    ("im home, did u see this?", "home"),
    ("im at sch rn, can u check smt for me?", "away"),
    ("gn, did the thing finish?", "asleep"),
])
def test_real_bug_a_statement_plus_a_question_in_the_same_message_is_still_read(text, mode):
    """2026-11-06: detect() used to reject the WHOLE message if '?' appeared anywhere, so a real
    statement of his location followed by an unrelated question was silently ignored."""
    assert pp.detect(text) == mode


def row(mid, text, author=ME, source="dm"):
    return {"id": mid, "text": text, "author_id": author, "source": source}


def test_only_his_own_new_dms_count_and_the_newest_hit_wins():
    rows = [row(1, "im at sch now"), row(2, "im home"), row(3, "gn", author=555), row(4, "gn", source="guild"), row(5, "lol ok")]
    assert pp.newest_hit(rows, 0) == ("home", 2)
    assert pp.newest_hit(rows, 2) == (None, None)
    assert pp.newest_hit(rows[:1], 0) == ("away", 1)


def test_first_run_sets_the_cursor_and_acts_on_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "LOGS", str(tmp_path))
    calls = []
    assert pp.run([], rows=[row(10, "im at sch now")], apply=lambda m: calls.append(m), snapshot=lambda m: calls.append(("snap", m)), current="home") == 0
    assert calls == [] and json.load(open(tmp_path / "presence_phrases_state.json"))["last_id"] == 10


def test_a_new_statement_sets_the_mode_once_takes_a_labelled_snapshot_and_moves_the_cursor(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "LOGS", str(tmp_path))
    (tmp_path / "presence_phrases_state.json").write_text(json.dumps({"last_id": 10}), encoding="utf-8")
    calls = []
    rows = [row(10, "old"), row(11, "lol ok"), row(12, "im at sch now lol")]
    pp.run([], rows=rows, apply=lambda m: calls.append(m), snapshot=lambda m: calls.append(("snap", m)), current="home")
    assert calls == ["away", ("snap", "away")] and json.load(open(tmp_path / "presence_phrases_state.json"))["last_id"] == 12
    calls.clear()
    pp.run([], rows=rows, apply=lambda m: calls.append(m), snapshot=lambda m: calls.append(("snap", m)), current="away")
    assert calls == []                                                                  # nothing new since the cursor


def test_a_statement_that_matches_the_current_mode_is_still_recorded_as_his_word_unless_it_already_was(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "LOGS", str(tmp_path))
    (tmp_path / "presence_phrases_state.json").write_text(json.dumps({"last_id": 10}), encoding="utf-8")
    (tmp_path / "presence_last_change.json").write_text(json.dumps({"mode": "away", "by": "auto", "ts": time.time()}), encoding="utf-8")
    calls = []
    pp.run([], rows=[row(11, "im at sch")], apply=lambda m: calls.append(m), snapshot=lambda m: None, current="away")
    assert calls == ["away"]                                                           # same mode, but now it counts as his word (longer hold)
    (tmp_path / "presence_phrases_state.json").write_text(json.dumps({"last_id": 10}), encoding="utf-8")
    (tmp_path / "presence_last_change.json").write_text(json.dumps({"mode": "away", "by": "phrase", "ts": time.time()}), encoding="utf-8")
    calls.clear()
    pp.run([], rows=[row(11, "im at sch")], apply=lambda m: calls.append(m), snapshot=lambda m: None, current="away")
    assert calls == []


def test_kill_switch_and_dry_run_change_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pp, "LOGS", str(tmp_path))
    (tmp_path / "presence_phrases_state.json").write_text(json.dumps({"last_id": 10}), encoding="utf-8")
    calls = []
    assert pp.run(["--dry-run"], rows=[row(11, "gn")], apply=lambda m: calls.append(m), snapshot=lambda m: None, current="home") == 0
    assert calls == [] and "would set asleep" in capsys.readouterr().out
    (tmp_path / "presence_phrases_off.txt").write_text("", encoding="utf-8")
    assert pp.run([], rows=[row(11, "gn")], apply=lambda m: calls.append(m), snapshot=lambda m: None, current="home") == 0 and calls == []


# ---------- presence_auto: phone messages are not location, and the holds ----------
NOW = 1_793_325_000.0


def sig(**kw):
    base = {"now_ts": NOW, "idle_s": 19000.0, "idle_source": "live", "own_session_kind": "console", "rdp_active": False, "game_on": False, "locked": False, "games": [],
            "phone": {"configured": False}, "prior": None, "last_discord_ts": NOW - 60, "last_telegram_ts": None, "last_ui_ts": None, "class_now": None}
    base.update(kw)
    return base


def daytime(monkeypatch):
    monkeypatch.setattr(pa.S, "TZ", pa.S.TZ)
    from datetime import datetime
    ts = datetime(2026, 10, 30, 10, 0, tzinfo=pa.S.TZ).timestamp()
    return ts


def test_chatting_from_the_phone_all_morning_does_not_read_as_home_and_a_long_quiet_pc_reads_as_away(monkeypatch):
    # 2026-11-05: found stale while testing an unrelated presence_auto.py change - this predates the 2026-10-31
    # "chatting weakens the idle-PC vote" fix (he chatted on his phone for 7h39m while the PC alone read away,
    # which was wrong). The code now deliberately caps idle-alone at the weaker quiet(2) vote while he's actively
    # messaging, never quiet-long(4), regardless of how long the PC itself has sat idle - the message from the
    # phone still adds no HOME vote, but it does cap the AWAY vote at the weaker tier.
    ts = daytime(monkeypatch)
    bd = pa.score(sig(now_ts=ts, last_discord_ts=ts - 30), "home", {}, ts)
    assert bd["totals"]["home"] == 0 and bd["totals"]["away"] == 2                      # PC quiet for 5 hours but he's actively chatting: capped at the weaker tier, not quiet-long
    assert not any(v["signal"] == "message" for v in bd["votes"])


def test_a_message_still_counts_when_the_pc_gives_no_reading(monkeypatch):
    ts = daytime(monkeypatch)
    bd = pa.score(sig(now_ts=ts, idle_s=None, last_discord_ts=ts - 30), "away", {}, ts)
    assert any(v["signal"] == "message" and v["mode"] == "home" for v in bd["votes"])


def test_a_hand_made_change_holds_45_minutes_his_own_words_hold_4_hours_auto_changes_do_not_hold(monkeypatch):
    ts = daytime(monkeypatch)
    s = sig(now_ts=ts)
    by_hand = {"mode": "home", "by": "manual", "ts": ts - 10 * 60}
    assert pa.decide(s, "home", {}, by_hand, ts)[0] is None
    stale_hand = {"mode": "home", "by": "manual", "ts": ts - 50 * 60}
    action, notes, streak, bd = pa.decide(s, "home", {}, stale_hand, ts)
    # 2026-11-05: found stale alongside the test above - the default sig() here has a recent discord message
    # (~11min old), so the same 2026-10-31 "chatting caps the idle-PC vote at the weaker tier" fix applies:
    # away never reaches MIN_TOP(4) on its own, so decide() proposes no candidate at all (streak stays {}),
    # it doesn't reach "away" as a first-of-two-checks candidate the way a non-chatting quiet PC would.
    assert action is None and not any("override" in n for n in notes) and streak == {}        # the hold is over: evidence counts again, but isn't strong enough alone while he's chatting
    his_words = {"mode": "home", "by": "phrase", "ts": ts - 3 * 3600}
    assert any("his words" in n for n in pa.decide(s, "home", {}, his_words, ts)[1])
    assert pa.decide(s, "home", {}, {"mode": "home", "by": "phrase", "ts": ts - 5 * 3600}, ts)[3] is not None
    assert not any("override" in n for n in pa.decide(s, "home", {}, {"mode": "home", "by": "phrase", "ts": ts - 5 * 3600}, ts)[1])


def test_typing_at_the_pc_overrules_a_hold_once_the_change_is_old_enough_not_to_flap(monkeypatch):
    ts = daytime(monkeypatch)
    at_pc = sig(now_ts=ts, idle_s=30.0)
    action = pa.decide(at_pc, "away", {}, {"mode": "away", "by": "phrase", "ts": ts - 35 * 60}, ts)[0]
    assert action and action["mode"] == "home"                                            # hold (4 h) is skipped by the explicit rule; the 30 min flip gap has passed
    held = pa.decide(at_pc, "away", {}, {"mode": "away", "by": "phrase", "ts": ts - 20 * 60}, ts)
    assert held[0] is None and any("held" in n for n in held[1])                          # not yet: flip gap
    just_now = pa.decide(at_pc, "away", {}, {"mode": "away", "by": "phrase", "ts": ts - 60}, ts)
    assert just_now[0] is None and any("his words" in n for n in just_now[1])             # inside the first 5 minutes the hold is absolute


# ---------- bedtime: his real bedtimes are late, so early-night silence is not enough ----------
def at(hour, minute):
    from datetime import datetime
    return datetime(2026, 10, 30, hour, minute, tzinfo=pa.S.TZ).timestamp()


def asleep_votes(hour, minute, idle_s, msg_age_s=3 * 3600):
    ts = at(hour, minute)
    bd = pa.score(sig(now_ts=ts, idle_s=float(idle_s), last_discord_ts=ts - msg_age_s), "home", {}, ts)
    return [v for v in bd["votes"] if v["signal"] == "quiet-night"]


def test_the_2325_case_58_quiet_minutes_before_midnight_is_not_sleep():
    assert asleep_votes(23, 25, 58 * 60) == []                                    # what switched him to asleep while he was awake studying
    assert asleep_votes(23, 59, 119 * 60) == []


def test_two_quiet_hours_before_midnight_still_count():
    assert len(asleep_votes(23, 40, 2 * 3600 + 60)) == 1


def test_between_midnight_and_one_the_bar_is_also_two_hours_then_it_drops_to_45_minutes():
    assert asleep_votes(0, 30, 58 * 60) == []
    assert len(asleep_votes(0, 30, 2 * 3600 + 60)) == 1
    assert asleep_votes(1, 30, 40 * 60) == []
    assert len(asleep_votes(1, 30, 46 * 60)) == 1


def test_a_late_evening_needs_the_message_gap_as_well():
    assert asleep_votes(23, 40, 3 * 3600, msg_age_s=20 * 60) == []               # silent PC but he messaged 20 minutes ago: not asleep

def test_a_missing_space_still_counts_imhome_and_imout():
    assert pp.detect('imhome') == 'home'
    assert pp.detect('imout rn') == 'away'
    assert pp.detect("i'm out rn and u didnt know") == 'away'
    assert pp.detect('immobile today') is None and pp.detect('imagine being home') is None
