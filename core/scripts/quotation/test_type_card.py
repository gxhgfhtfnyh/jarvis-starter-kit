"""Tests for type_card, with the real RE1392 reading of 2026-10-29 as the main fixture."""
import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import type_card as tc  # noqa: E402

RE1392 = {
    "pdf": "RE1392.pdf", "pages": 6, "pages_read": [1, 2, 3, 4, 5, 6], "re_no": "RE1392", "title": "RADIATOR COOLING SECTION", "overall_w": 1384, "overall_h": 1512,
    "features": {
        "fan": {"value": False, "page": 1}, "plenum": {"value": False, "page": 1}, "cowl": {"value": False, "page": 1}, "belt_drive": {"value": False, "page": 1},
        "cores": {"value": 1, "page": 1}, "orientation": {"value": "vertical", "page": 1}, "core_guard": {"value": True, "page": 6},
        "tank_material": {"value": "carbon", "page": 2}, "cap_breather": {"value": True, "page": 2}, "coolant_note": {"value": True, "page": 1},
    },
    "ejw_ac_evidence": [{"call": "EJW", "page": 2, "note": "neck and cap detail, 8 mm breathe hole, A36 3 mm"}, {"call": "EJW", "page": 1, "note": "coolant capacity 55 L"}],
    "ejw_ac_call": "EJW",
}


def test_the_real_re1392_card_is_valid_and_infers_ejw_cooling_section_with_core_guard():
    assert tc.validate(RE1392) == []
    key, variant, trace = tc.infer(RE1392)
    assert key == "cooling" and variant == "EJW c/w core guard"
    assert any("cap/breather" in t for t in trace)


def test_unread_pages_are_refused_which_is_the_2026_10_29_mistake():
    card = copy.deepcopy(RE1392)
    card["pages_read"] = [1]
    assert any("pages not read: [2, 3, 4, 5, 6]" in b for b in tc.validate(card))


def test_unknown_needs_clues_from_two_different_pages():
    card = copy.deepcopy(RE1392)
    card["ejw_ac_call"] = "UNKNOWN"
    card["ejw_ac_evidence"] = [{"call": "EJW", "page": 1, "note": "x"}]
    assert any("UNKNOWN is not accepted" in b for b in tc.validate(card))
    card["ejw_ac_evidence"] = [{"call": "EJW", "page": 1, "note": "x"}, {"call": "AC", "page": 3, "note": "y"}]
    assert not any("UNKNOWN" in b for b in tc.validate(card))


def test_a_call_needs_evidence_pointing_to_it():
    card = copy.deepcopy(RE1392)
    card["ejw_ac_call"] = "AC"
    assert any("call AC needs" in b for b in tc.validate(card))


@pytest.mark.parametrize("name", tc.FEATURES)
def test_every_feature_needs_a_value_and_a_page(name):
    card = copy.deepcopy(RE1392)
    card["features"][name] = {"value": True}
    assert any(name in b for b in tc.validate(card))


def test_feature_page_must_exist():
    card = copy.deepcopy(RE1392)
    card["features"]["fan"]["page"] = 9
    assert any("outside 1..6" in b for b in tc.validate(card))


@pytest.mark.parametrize("changes,key", [
    ({"belt_drive": True}, "belt"), ({"belt_drive": True, "cores": 2}, "belt2"), ({"cowl": True}, "cowl"), ({"cowl": True, "cores": 2}, "cowl2"),
    ({"orientation": "horizontal"}, "horizontal"), ({}, "cooling"),
])
def test_infer_section_rules(changes, key):
    card = copy.deepcopy(RE1392)
    for k, v in changes.items():
        card["features"][k]["value"] = v
    assert tc.infer(card)[0] == key


def test_ac_reading_from_stainless_tanks_without_a_cap():
    card = copy.deepcopy(RE1392)
    card["features"].update({"tank_material": {"value": "stainless", "page": 2}, "cap_breather": {"value": False, "page": 2}, "coolant_note": {"value": False, "page": 1}, "core_guard": {"value": False, "page": 6}})
    card["ejw_ac_call"], card["ejw_ac_evidence"] = "AC", [{"call": "AC", "page": 2, "note": "4 mm stainless 316, air pipe collar"}]
    assert tc.validate(card) == []
    assert tc.infer(card)[1] == "AC"


def test_new_card_renders_pages(tmp_path):
    import glob
    pdfs = glob.glob(r"J:\Jarvis2\logs\discord_media\*RE1392*.pdf")
    if not pdfs:
        pytest.skip("RE1392 pdf not present")
    out = tmp_path / "card.json"
    tc.new_card(pdfs[0], str(out))
    import json
    card = json.load(open(out, encoding="utf-8"))
    assert card["pages"] == 6 and card["pages_read"] == [] and tc.validate(card)
    assert len(os.listdir(str(out)[:-5] + "_pages")) == 6
