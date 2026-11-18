# -*- coding: utf-8 -*-
"""Blind test of the quotation method (2026-10-29): quote an old job with that job's own price history hidden, then compare to what was really charged.
    python blind_test.py cases.json [--prices prices.json]
cases.json is a list of {"re_no": "RE1964", "section": "Radiator Cooling Section", "w": 2646, "h": 2864, "actual_price": 16000,
                         "core_guard": true, "sheet_w": 2646, "sheet_h": 2864 (optional)}
  w, h        the width and height read off the customer's drawing (the drawing is the authority)
  section     which section of the price list the item belongs to (a section name from prices.json)
  core_guard  add the core guard allowance (rate_method.CORE_GUARD_ALLOWANCE) on top of the method price
  actual_price what was really charged (from the owner's past quotation)
Every record of the same RE number is removed from the history before predicting, so the method never sees the answer.
Prints one line per case (predicted, actual, percent error, size flag) and the median / mean absolute error. Read-only. Pure helpers are unit tested."""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rate_method as rm  # noqa: E402

DEFAULT_PRICES = r"J:\Jarvis2\logs\quotation_data\prices.json"


def pct_error(predicted, actual):
    """Signed percent error of the prediction against the real price (positive = I priced higher)."""
    return 100.0 * (predicted - actual) / actual


def run_case(case, records):
    """Result dict for one case: predicted price (None when the method cannot price it), error percent and a size note."""
    history = [r for r in records if r.get("re_no") != case["re_no"].upper()]
    area = case["w"] * case["h"] / 1e6
    base = rm.predict({"section": case["section"], "area_m2": area, "unit_price": 0, "job": "blind"}, history)
    predicted = None if base is None else (rm.with_core_guard(base) if case.get("core_guard") else base)
    note = ""
    if case.get("sheet_w") and case.get("sheet_h") and (case["w"], case["h"]) != (case["sheet_w"], case["sheet_h"]):
        note = "size differs from sheet (%dx%d)" % (case["sheet_w"], case["sheet_h"])
    return {"re_no": case["re_no"], "predicted": predicted, "actual": case["actual_price"],
            "error_pct": None if predicted is None else pct_error(predicted, case["actual_price"]), "size_note": note}


def summary(results):
    errs = [abs(r["error_pct"]) for r in results if r["error_pct"] is not None]
    if not errs:
        return {"n": 0, "median_abs": None, "mean_abs": None, "within_10": 0}
    return {"n": len(errs), "median_abs": statistics.median(errs), "mean_abs": sum(errs) / len(errs), "within_10": sum(1 for e in errs if e <= 10)}


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    path = argv[argv.index("--prices") + 1] if "--prices" in argv else DEFAULT_PRICES
    records = json.load(open(path, encoding="utf-8"))
    cases = json.load(open(argv[0], encoding="utf-8"))
    results = [run_case(c, records) for c in cases]
    for r in results:
        if r["predicted"] is None:
            print("%-8s cannot price (no comparable jobs in that section)" % r["re_no"])
        else:
            print("%-8s predicted %8.0f  actual %8.0f  error %+6.1f%%  %s" % (r["re_no"], r["predicted"], r["actual"], r["error_pct"], r["size_note"]))
    s = summary(results)
    if s["n"]:
        print("n=%d  median abs error %.1f%%  mean abs error %.1f%%  within 10%%: %d of %d" % (s["n"], s["median_abs"], s["mean_abs"], s["within_10"], s["n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
