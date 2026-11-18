"""Tests for blind_test. Run: python -m pytest -q test_blind_test.py (from the quotation folder)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import blind_test as bt  # noqa: E402


def rec(re_no, area, price, section="S"):
    return {"re_no": re_no, "section": section, "area_m2": area, "unit_price": price, "rate_per_m2": price / area}


def test_pct_error_sign_and_size():
    assert bt.pct_error(110, 100) == pytest.approx(10.0)
    assert bt.pct_error(90, 100) == pytest.approx(-10.0)


def test_own_history_is_hidden():
    # the case's own RE record is priced absurdly low; if it leaked in, the prediction would collapse
    recs = [rec("RE1", 2.0, 1), rec("RE2", 2.0, 2000), rec("RE3", 3.0, 3000), rec("RE4", 4.0, 4000)]
    r = bt.run_case({"re_no": "re1", "section": "S", "w": 1000, "h": 2000, "actual_price": 2000}, recs)
    assert r["predicted"] == 2000 and abs(r["error_pct"]) < 0.01


def test_core_guard_adds_the_allowance():
    recs = [rec("RE2", 2.0, 2000), rec("RE3", 3.0, 3000), rec("RE4", 4.0, 4000)]
    plain = bt.run_case({"re_no": "RE1", "section": "S", "w": 1000, "h": 2000, "actual_price": 2000}, recs)["predicted"]
    guard = bt.run_case({"re_no": "RE1", "section": "S", "w": 1000, "h": 2000, "actual_price": 2000, "core_guard": True}, recs)["predicted"]
    assert guard == plain + 500


def test_cannot_price_without_comparables_and_size_note():
    r = bt.run_case({"re_no": "RE9", "section": "Nowhere", "w": 1000, "h": 1000, "actual_price": 1, "sheet_w": 1000, "sheet_h": 1100}, [rec("RE2", 1.0, 1)])
    assert r["predicted"] is None and r["error_pct"] is None and "differs" in r["size_note"]


def test_summary():
    s = bt.summary([{"error_pct": 5.0}, {"error_pct": -15.0}, {"error_pct": None}])
    assert s["n"] == 2 and s["median_abs"] == 10.0 and s["mean_abs"] == 10.0 and s["within_10"] == 1
    assert bt.summary([])["n"] == 0
