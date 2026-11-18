"""Tests for rate_method. Run: python -m pytest -q test_rate_method.py (from the quotation folder)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rate_method as rm  # noqa: E402


@pytest.mark.parametrize("x,y", [(0, 0), (1, 50), (50, 50), (50.01, 100), (12601, 12650), (12650, 12650), (12150.0, 12150), (12199.99, 12200)])
def test_round_up_50(x, y):
    assert rm.round_up_50(x) == y


def rec(section, area, price, name="x"):
    return {"section": section, "area_m2": area, "unit_price": price, "rate_per_m2": price / area if area else None, "job": name}


def test_predict_uses_same_section_nearest_median_rate():
    a = rec("A", 2.0, 10000)           # rate 5000
    b = rec("A", 3.0, 13500)           # rate 4500
    c = rec("A", 10.0, 90000)          # rate 9000 (far in area)
    d = rec("B", 2.0, 99999)           # other section: ignored
    target = rec("A", 2.5, 0)
    assert rm.predict(target, [a, b, c, d, target], k=2) == rm.round_up_50(2.5 * 4750)   # median of 5000 and 4500


def test_predict_excludes_itself_and_needs_two_comparables():
    a = rec("A", 2.0, 10000)
    b = rec("A", 3.0, 13500)
    assert rm.predict(a, [a, b]) is None                   # only one comparable
    assert rm.predict(a, [a, b, rec("A", 4.0, 20000)]) is not None


def test_predict_none_without_area():
    a = rec("A", None, 1000)
    assert rm.predict(a, [a, rec("A", 1.0, 1), rec("A", 2.0, 2)]) is None


def test_evaluate_reports_percent_error_and_skips_unpredictable():
    rs = [rec("A", 1.0, 1000), rec("A", 2.0, 2000), rec("A", 3.0, 3000), rec("Z", 1.0, 500)]
    ev = rm.evaluate(rs)
    assert len(ev) == 3 and all(abs(e) < 0.01 for _, _, e in ev)   # exact linear data: zero error (all rates 1000/m2)
    assert all(r["section"] == "A" for r, _, _ in ev)


def test_evaluate_error_sign():
    rs = [rec("A", 1.0, 1000), rec("A", 2.0, 2000), rec("A", 3.0, 3000), rec("A", 2.5, 1000)]   # the last one is a bargain
    errs = {r["unit_price"]: e for r, _, e in rm.evaluate(rs)}
    assert errs[1000] > 0                                  # predicted higher than the real (cheap) price


def test_core_guard_allowance_is_an_addon():
    assert rm.with_core_guard(12150) == 12650            # the chat benchmark pair
    assert rm.with_core_guard(12150, 1500) == 13650


def _rec(re_no, desc, price, w, h, section="Radiator Cooling Section"):
    area = w * h / 1e6
    return {"re_no": re_no, "description": desc, "unit_price": price, "w_mm": w, "h_mm": h, "area_m2": area, "rate_per_m2": price / area, "section": section}


SHEET = [_rec("RE1392", "J3011392 Cooling Section", 11350.0, 1384, 1512), _rec("RE1", "a", 10000.0, 1300, 1500), _rec("RE2", "b", 12000.0, 1500, 1600), _rec("RE3", "c", 11000.0, 1400, 1450)]


def test_exact_sheet_row_beats_the_method_and_core_guard_adds_the_usual_allowance():
    r = rm.price_with_basis("RE1392", "Radiator Cooling Section", 1384, 1512, SHEET, core_guard=True)
    assert r["final"] == 11850 and r["low"] == 11850 and r["high"] == 12850 and r["basis"].startswith("sheet row")
    assert r["method"] is not None and r["method"] != r["base"]


def test_without_core_guard_the_sheet_price_is_the_answer_and_it_warns_about_the_guard():
    r = rm.price_with_basis("RE1392", "Radiator Cooling Section", 1384, 1512, SHEET)
    assert r["final"] == 11350 and r["low"] == r["high"] == 11350 and any("does not say c/w core guard" in n for n in r["notes"])


def test_a_row_that_already_has_the_guard_adds_nothing():
    recs = [_rec("RE5", "J1 Cooling Section c/w core guard", 12000.0, 1500, 1500)] + SHEET[1:]
    r = rm.price_with_basis("RE5", "Radiator Cooling Section", 1500, 1500, recs, core_guard=True)
    assert r["final"] == 12000 and any("already says" in n for n in r["notes"])


def test_unknown_re_falls_back_to_the_method_and_says_so():
    r = rm.price_with_basis("RE9999", "Radiator Cooling Section", 1400, 1500, SHEET)
    assert r["basis"].startswith("method") and r["final"] is not None


def test_sheet_row_of_a_different_size_is_not_used():
    r = rm.price_with_basis("RE1392", "Radiator Cooling Section", 1700, 1800, SHEET)
    assert r["basis"].startswith("method")


def test_cannot_price_is_explicit():
    r = rm.price_with_basis("RE9999", "Nowhere", 1000, 1000, SHEET)
    assert r["final"] is None and "cannot price" in r["notes"]
