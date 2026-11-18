# -*- coding: utf-8 -*-
"""Tests for send_gate_hook.py's owner-id-from-config change (the kit's version reads logs/discord_config.json
via discord_queue_send.owner_id() instead of a hardcoded constant, since a kit install has no owner baked in)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import send_gate_hook as sg  # noqa: E402


def _write_config(logs_dir, owner_id):
    os.makedirs(logs_dir, exist_ok=True)
    with open(os.path.join(logs_dir, "discord_config.json"), "w", encoding="utf-8") as f:
        json.dump({"owner_id": owner_id}, f)


def test_owner_id_reads_the_real_configured_value(tmp_path, monkeypatch):
    _write_config(tmp_path, 42)
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    assert sg._owner_id() == 42


def test_owner_id_is_none_when_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    assert sg._owner_id() is None


def test_discord_classify_fails_closed_when_owner_not_configured(tmp_path, monkeypatch):
    """A send gate with no owner_id yet must never treat anything as the standing owner exception."""
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    cls = sg._discord_one("stmt", lookup=None, us=None, ch=None, rt=None)
    assert cls["external"] is True
    assert cls["standing"] is False
    assert cls["kind"] == "discord_not_configured"


def test_discord_classify_recognizes_the_owner_once_configured(tmp_path, monkeypatch):
    _write_config(tmp_path, 42)
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    cls = sg._discord_one("stmt", lookup=None, us="42", ch=None, rt=None)
    assert cls["external"] is False


def test_discord_classify_treats_a_different_user_as_external_once_configured(tmp_path, monkeypatch):
    _write_config(tmp_path, 42)
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    cls = sg._discord_one("stmt", lookup=None, us="99", ch=None, rt=None)
    assert cls["external"] is True


def test_approval_evidence_never_approves_when_owner_not_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(sg, "LOGS", str(tmp_path))
    dms = [{"id": 1, "author_id": None, "source": "dm", "date": "2026-11-18T00:00:00+00:00", "text": "ABC123"}]
    result = sg.approval_evidence(dms, since=0, now=10**12, code="ABC123")
    assert result is None


