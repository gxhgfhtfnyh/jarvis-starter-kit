"""Tests for price_list helpers. Run: python -m pytest -q test_price_list.py (from the quotation folder)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import price_list as pl  # noqa: E402


@pytest.mark.parametrize("text,wh", [("2646 x 2864", (2646, 2864)), ("1500X1696", (1500, 1696)), (" 3222 * 3068 ", (3222, 3068)),
                                     ("big", None), ("12 x 34", None), (None, None), ("", None)])
def test_parse_wh(text, wh):
    assert pl.parse_wh(text) == wh


@pytest.mark.parametrize("desc,ref", [("J11031964 EJW Cooling section c/w core guard", ("J11031964", "RE1964")), ("j7081681 Cooling", ("J7081681", "RE1681")),
                                      ("RE1080 Cooling Section", (None, None)), (None, (None, None)), ("Radiator Cooling Section", (None, None))])
def test_job_ref(desc, ref):
    assert pl.job_ref(desc) == ref


def test_tags():
    assert pl.tags_of("J15022064 - Cowling radiator double section") == ["double", "cowling"]
    assert pl.tags_of("J11031964 EJW Cooling section c/w core guard exclude core") == ["ejw", "core_guard", "exclude_core"]
    assert "ac" in pl.tags_of("J2101357 AC Cooling Section")
    assert pl.tags_of("Horizontal radiator") == ["horizontal"]
    assert pl.tags_of(None) == []


def test_records_sections_prices_and_rates():
    rows = [["UNIT PRICE LIST A"], [None, "ITEM"], [None, "Radiator Cooling Section", None, None, None, None],
            [None, "J1 Header Without Price", None, None, None, None, None],
            [None, "J11031964 EJW Cooling section c/w core guard exclude core", "16000", "17280", "Benchmark", "2646 x 2864", "7.5781"],
            [None, "J11031964 Horizontal radiator", "37700", "40716", "1 SET", "2748 x 2754", "7.567"],
            [None, "No size row", "100", None, None, None, None]]
    recs = pl.records(rows)
    assert [r["description"][:9] for r in recs] == ["J11031964", "J11031964", "No size r"]
    first = recs[0]
    assert first["section"] == "J1 Header Without Price" and first["re_no"] == "RE1964"
    assert first["w_mm"] == 2646 and first["h_mm"] == 2864 and first["unit_price"] == 16000.0 and first["price_up8"] == 17280.0
    assert first["rate_per_m2"] == round(16000 / 7.5781, 1) and first["benchmark"] == "Benchmark"
    assert recs[2]["rate_per_m2"] is None and recs[2]["w_mm"] is None


def test_area_computed_from_size_when_missing():
    recs = pl.records([[None, "J1 X", "1000", None, None, "1000 x 2000", None]])
    assert recs[0]["area_m2"] == 2.0 and recs[0]["rate_per_m2"] == 500.0


def test_rows_with_price_text_that_is_not_a_number_are_skipped():
    assert pl.records([[None, "J1 X", "TBC", None, None, "1000 x 1000", None]]) == []


def test_priceless_job_row_does_not_start_a_new_section():
    rows = [[None, "Radiator Cooling Section", None, None, None, None, None],
            [None, "J2101357 EJW Cooling Section c/w core guard", None, None, None, None, None],
            [None, "J3011386 Cooling Section", "12500", None, None, "1500 x 1637", "2.4555"]]
    recs = pl.records(rows)
    assert len(recs) == 1 and recs[0]["section"] == "Radiator Cooling Section"
