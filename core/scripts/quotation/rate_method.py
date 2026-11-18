# -*- coding: utf-8 -*-
"""The owner's pricing method as code (2026-10-29), plus a leave-one-out test of how accurate it is on his own price history.
Method (from the ChatGPT chat, see the vault note 'Quotation Pipeline - Method from the ChatGPT Chat'): area = width x height; take the rate
(price per m2) of comparable historic jobs; price = area x rate; round UP to the nearest S$50.
    python rate_method.py prices.json            # leave-one-out accuracy per section and overall
    python rate_method.py prices.json RE1964     # leave-one-out prediction for the records of one drawing number
Comparables = other records in the SAME section with a size, nearest by area (k=3), median rate. This is only the baseline method; it ignores
tags such as core guard or exclude-core, which the next version should use."""
import json
import math
import statistics
import sys


def round_up_50(x):
    """Round UP to the nearest 50 (an exact multiple stays)."""
    return int(math.ceil(round(x / 50.0, 9)) * 50)


def predict(rec, records, k=3):
    """Predicted price for rec from comparable records (same section, has area and rate, not rec itself); None when fewer than 2 comparables."""
    comps = [r for r in records if r is not rec and r.get("section") == rec.get("section") and r.get("area_m2") and r.get("rate_per_m2")]
    if len(comps) < 2 or not rec.get("area_m2"):
        return None
    comps.sort(key=lambda r: abs(r["area_m2"] - rec["area_m2"]))
    rate = statistics.median(r["rate_per_m2"] for r in comps[:k])
    return round_up_50(rec["area_m2"] * rate)


def evaluate(records, k=3):
    """List of (record, predicted, pct_error) for every record that can be predicted."""
    out = []
    for r in records:
        if not (r.get("unit_price") and r.get("area_m2")):
            continue
        p = predict(r, records, k)
        if p is not None:
            out.append((r, p, 100.0 * (p - r["unit_price"]) / r["unit_price"]))
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    recs = json.load(open(argv[0], encoding="utf-8"))
    ev = evaluate(recs)
    if len(argv) > 1:
        for r, p, e in ev:
            if r.get("re_no") == argv[1].upper():
                print("%-12s %-55s area %.2f  actual %7.0f  predicted %7d  error %+.1f%%" % (r["job"], r["description"][:55], r["area_m2"], r["unit_price"], p, e))
        return 0
    secs = {}
    for r, p, e in ev:
        secs.setdefault(r["section"], []).append(abs(e))
    print("%d records predicted; overall median abs error %.1f%%, mean %.1f%%; within 10%%: %d%%, within 20%%: %d%%" % (
        len(ev), statistics.median(abs(e) for _, _, e in ev), statistics.mean(abs(e) for _, _, e in ev),
        100 * sum(1 for _, _, e in ev if abs(e) <= 10) // len(ev), 100 * sum(1 for _, _, e in ev if abs(e) <= 20) // len(ev)))
    for s, errs in sorted(secs.items(), key=lambda kv: -len(kv[1])):
        print("  %-52s n=%3d median abs error %5.1f%%" % (s[:52], len(errs), statistics.median(errs)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))


CORE_GUARD_ALLOWANCE = 500   # EJW c/w core guard = AC price + 500 on 4 of 8 same-size pairs in the sheet (600 once, 1500 three times); see Experiment Log 2026-10-29


def with_core_guard(ac_price, allowance=CORE_GUARD_ALLOWANCE):
    """EJW c/w core guard price from the AC/cooling-section price; the allowance is an add-on, not part of the rate per m2."""
    return ac_price + allowance


CORE_GUARD_RANGE = (500, 1500)   # observed EJW c/w core guard minus AC on the sheet: 500 (4 pairs), 600 (1), 1500 (3)


def price_with_basis(re_no, section, w, h, records, core_guard=False, tol=0.02):
    """One place that answers 'what price and how sure': (2026-10-29: I quoted RE1392 mixing the sheet price with a method estimate.)
    Basis order: the same RE number on the sheet at the same size (within tol) beats the method estimate. The core guard allowance is added only when
    asked for AND the sheet row does not already say c/w core guard; the range runs from the usual 500 to the largest seen 1500.
    Returns {final, basis, base, method, low, high, notes}."""
    area = w * h / 1e6
    others = [r for r in records if r.get("re_no") != re_no.upper()]
    method = predict({"section": section, "area_m2": area, "unit_price": 0, "job": "new"}, others)
    own = [r for r in records if r.get("re_no") == re_no.upper() and r.get("w_mm") and r.get("h_mm")
           and abs(r["w_mm"] - w) / w <= tol and abs(r["h_mm"] - h) / h <= tol]
    notes = []
    if own:
        row = own[0]
        base, basis = row["unit_price"], "sheet row '%s'" % row["description"]
        has_guard = "core guard" in row["description"].lower()
        notes.append("price is what the sheet shows for this exact job and size")
    else:
        base, basis, has_guard = method, "method (no sheet row for this RE at this size)", False
        notes.append("no sheet row: estimate from the nearest jobs in the section")
    if base is None:
        return {"final": None, "basis": basis, "base": None, "method": method, "low": None, "high": None, "notes": notes + ["cannot price"]}
    low = high = base
    if core_guard and not has_guard:
        low, high = base + CORE_GUARD_RANGE[0], base + CORE_GUARD_RANGE[1]
        notes.append("core guard added: usual +%d, up to +%d on some jobs" % CORE_GUARD_RANGE)
    elif core_guard:
        notes.append("the sheet row already says c/w core guard: nothing added")
    elif not has_guard:
        notes.append("the sheet row does not say c/w core guard; if the guard is included the price is higher")
    return {"final": round_up_50(low), "basis": basis, "base": base, "method": method, "low": round_up_50(low), "high": round_up_50(high), "notes": notes}
