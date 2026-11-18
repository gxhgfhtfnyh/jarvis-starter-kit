# -*- coding: utf-8 -*-
"""Compare the width x height read off a customer drawing with the price list sheet, for the quotation pipeline (2026-10-29).
    python size_check.py RE1964 2646 2864 [--prices prices.json]
Prints one line per sheet record with that RE number: OK, or MISMATCH with the difference in mm and percent. The drawing is the authority;
a mismatch means 'look again / tell the owner', not 'trust the sheet'. Pure helper compare() is unit tested."""
import json
import sys

DEFAULT_PRICES = r"J:\Jarvis2\logs\quotation_data\prices.json"


def compare(w, h, sheet_w, sheet_h, tol_pct=0.3):
    """('OK'|'MISMATCH'|'NO SHEET SIZE', detail). Width and height are each compared by their own percent difference."""
    if not sheet_w or not sheet_h:
        return "NO SHEET SIZE", "sheet has no size for this item"
    dw, dh = w - sheet_w, h - sheet_h
    pw, ph = 100.0 * abs(dw) / sheet_w, 100.0 * abs(dh) / sheet_h
    detail = "drawing %dx%d vs sheet %dx%d (W %+d mm, H %+d mm)" % (w, h, sheet_w, sheet_h, dw, dh)
    return ("OK" if max(pw, ph) <= tol_pct else "MISMATCH"), detail


def check(re_no, w, h, records, tol_pct=0.3):
    """One (status, description, detail) per record whose RE number matches; empty list when the RE number is unknown."""
    return [compare(w, h, r.get("w_mm"), r.get("h_mm"), tol_pct) + (r["description"],) for r in records if r.get("re_no") == re_no.upper()]


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 1
    path = argv[argv.index("--prices") + 1] if "--prices" in argv else DEFAULT_PRICES
    recs = json.load(open(path, encoding="utf-8"))
    out = check(argv[0], int(argv[1]), int(argv[2]), recs)
    if not out:
        print("%s is not on the sheet: no size to compare" % argv[0].upper())
        return 0
    for status, detail, desc in out:
        print("%-13s %s | %s" % (status, desc[:55], detail))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
