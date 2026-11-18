# -*- coding: utf-8 -*-
"""Security review (2026-11-18): SAFE_VALUE_WORDS was a substring check applied to EVERY pattern, including the
high-confidence specific token formats -- a real AWS key, GitHub token etc. sitting on a line that happened to
contain one of these common words anywhere (even inside an unrelated word, like "reset" containing "set") would be
silently hidden. Fixed to whole-word matching, and restricted to only the generic/low-confidence patterns."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import secret_scanner as ss  # noqa: E402


def _scan_text(tmp_path, text, name="f.py"):
    (tmp_path / name).write_text(text, encoding="utf-8")
    return ss.scan(root=tmp_path, glob_pattern="*.py")


def test_a_real_aws_key_is_never_hidden_by_a_nearby_safe_word(tmp_path):
    findings = _scan_text(tmp_path, 'AWS_KEY = "AKIA1234567890ABCDEF"  # not rotated yet, stored carelessly\n')
    assert any(f["pattern"] == "AWS access key" for f in findings)


def test_a_real_github_token_is_never_hidden_by_a_substring_match(tmp_path):
    findings = _scan_text(tmp_path, 'token = "ghp_abcdefghijklmnopqrstuvwxyz123456"  # was reset last week\n')
    assert any(f["pattern"] == "GitHub token" for f in findings)


def test_a_real_private_key_block_is_never_hidden(tmp_path):
    findings = _scan_text(tmp_path, "-----BEGIN RSA PRIVATE KEY-----\nnever commit this, keep it stored safely\n")
    assert any(f["pattern"] == "Private key block" for f in findings)


def test_generic_password_prose_is_still_suppressed_on_a_real_whole_word_match(tmp_path):
    findings = _scan_text(tmp_path, "password: never store this in plaintext, use keyring instead\n")
    assert not any(f["pattern"] == "Generic password assignment" for f in findings)


def test_generic_pattern_substring_false_match_no_longer_wrongly_suppresses(tmp_path):
    """'reset' contains 'set' as a substring -- the old check would have wrongly treated this as safe."""
    findings = _scan_text(tmp_path, 'api_key = "abcdefghijklmnopqrstuvwxyz123456"  # was reset last week\n')
    assert any(f["pattern"] == "Generic API key assignment" for f in findings)
