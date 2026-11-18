"""Tests for size_check. Run: python -m pytest -q test_size_check.py (from the quotation folder)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import size_check as sc  # noqa: E402


def test_exact_match_ok():
    assert sc.compare(2646, 2864, 2646, 2864)[0] == "OK"


def test_small_difference_within_tolerance_ok():
    assert sc.compare(1213, 1527, 1212, 1527)[0] == "OK"          # 1 mm of 1212 is 0.08 percent


@pytest.mark.parametrize("h", [3088, 3065])
def test_real_mismatch_from_re1978_flags(h):
    status, detail = sc.compare(3222, h, 3222, 3068)
    assert (status == "MISMATCH") == (abs(h - 3068) / 3068 * 100 > 0.3)
    assert "H %+d mm" % (h - 3068) in detail


def test_re1681_height_mismatch():
    assert sc.compare(1213, 1501, 1212, 1527)[0] == "MISMATCH"      # 1.7 percent


def test_missing_sheet_size():
    assert sc.compare(1000, 1000, None, 500)[0] == "NO SHEET SIZE"


def test_check_matches_re_number_case_insensitively_and_unknown_is_empty():
    recs = [{"re_no": "RE1964", "description": "a", "w_mm": 2646, "h_mm": 2864}, {"re_no": "RE1681", "description": "b", "w_mm": 1, "h_mm": 1}]
    assert [x[2] for x in sc.check("re1964", 2646, 2864, recs)] == ["a"]
    assert sc.check("RE9999", 1, 1, recs) == []
