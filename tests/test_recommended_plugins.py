"""Tests for the owner's direct ask (2026-11-19, "might wanna make his AI automatically install the skills and
plugins that we have") -- a fresh assistant starts with a real, judged shortlist of marketplace plugins already
switched on, instead of an empty memory with nothing but the Lessons Pack. Two real regressions were caught and
fixed building this (see generate.py's RECOMMENDED_PLUGINS / _with_recommended_plugins docstrings): a bare
{{placeholder}} as a JSON value broke update_assistant.py's raw .tpl parsing, and a separate post-step broke
generate()'s own idempotency guarantee on a repeat run."""
import json
import os
import sys

import pytest

KIT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, KIT)
import generate as G  # noqa: E402

ANSWERS = {"name": "Ann Lee", "assistant_name": "Penny"}


def test_fresh_build_enables_the_recommended_plugins_and_keeps_the_hooks(tmp_path):
    out = tmp_path / "built"
    G.generate(KIT, ANSWERS, out)
    data = json.loads((out / ".claude" / "settings.json").read_text(encoding="utf-8"))
    assert data["enabledPlugins"] == G.RECOMMENDED_PLUGINS
    assert data["extraKnownMarketplaces"] == G.RECOMMENDED_MARKETPLACES
    assert "hooks" in data and "Stop" in data["hooks"]           # the real hook wiring must survive the merge


def test_a_repeat_run_is_idempotent_settings_json_is_skipped_not_overwritten(tmp_path):
    out = tmp_path / "built"
    G.generate(KIT, ANSWERS, out)
    again = G.generate(KIT, ANSWERS, out)
    assert ".claude/settings.json" in again["skipped"]
    assert ".claude/settings.json" not in again["created"] and ".claude/settings.json" not in again["overwritten"]


def test_the_raw_tpl_has_no_unrendered_placeholder_and_stays_valid_json_on_its_own():
    """The regression this guards: injecting a bare {{placeholder}} as a JSON value makes the .tpl's own raw,
    unrendered text invalid JSON, which broke update_assistant.py (it parses the .tpl directly to compare hook
    structures against an existing assistant's settings.json, not just the rendered output)."""
    tpl_path = os.path.join(KIT, "core", ".claude", "settings.json.tpl")
    raw = open(tpl_path, encoding="utf-8").read()
    assert "recommended_plugins_json" not in raw and "recommended_marketplaces_json" not in raw
    json.loads(raw)  # must parse as plain JSON even before any placeholder rendering (the remaining
    # {{python_exe}}/{{root_path}} placeholders are fine: they sit inside already-quoted string values)


def test_with_recommended_plugins_merges_without_clobbering_existing_keys():
    base = json.dumps({"hooks": {"Stop": []}, "enabledPlugins": {"some-other@mp": True}}).encode("utf-8")
    merged = json.loads(G._with_recommended_plugins(base).decode("utf-8"))
    assert merged["hooks"] == {"Stop": []}
    assert merged["enabledPlugins"]["some-other@mp"] is True       # a pre-existing entry survives the merge
    assert merged["enabledPlugins"]["superpowers@claude-plugins-official"] is True


def test_with_recommended_plugins_is_deterministic():
    base = json.dumps({"hooks": {}}).encode("utf-8")
    assert G._with_recommended_plugins(base) == G._with_recommended_plugins(base)
