# -*- coding: utf-8 -*-
"""Drawing reading card (2026-10-29). Why it exists: asked 'which type of radiator is this?' for RE1392 I classified from the title block and page 1,
said 'I cannot tell EJW from AC', and was wrong: the part pages (neck-and-cap detail, A36 tanks, coolant note) show it is EJW. The card makes that
shortcut impossible to repeat quietly: every page of the drawing must be listed as read, every feature carries the page it was seen on, and an UNKNOWN
EJW/AC call is refused unless clues were looked for on at least two different part pages.
    python type_card.py new  drawing.pdf  card.json     # renders every page to PNG next to the card and writes the empty card
    python type_card.py check card.json                  # validates coverage and evidence, prints the suggested type with the rule trace
Card = {"pdf": path, "pages": N, "pages_read": [1..N], "re_no": "RE1392", "title": "...", "overall_w": 1384, "overall_h": 1512,
        "features": {name: {"value": ..., "page": n}}, "ejw_ac_evidence": [{"call": "EJW"|"AC", "page": n, "note": "..."}], "ejw_ac_call": "EJW"|"AC"|"UNKNOWN"}
Feature names: fan, plenum, cowl, belt_drive (bool), cores (int), orientation ('vertical'|'horizontal'), core_guard (bool), tank_material ('carbon'|'stainless'),
cap_breather (bool), coolant_note (bool). The vision part (reading the pages) is mine; the rules below are code. Pure core (unit tested): validate(card), infer(card)."""
import json
import os
import sys

FEATURES = ("fan", "plenum", "cowl", "belt_drive", "cores", "orientation", "core_guard", "tank_material", "cap_breather", "coolant_note")
SECTIONS = {"cooling": "Radiator Cooling Section", "horizontal": "Horizontal Remote Radiator Cooling Section", "belt": "Radiator Belt Driven design",
            "belt2": "Radiator Belt Driven design double section (CAT)", "cowl": "Radiator Cowling design", "cowl2": "Radiator Cowling design double section"}


def validate(card, page_count=None):
    """List of problems; empty means the card is complete enough to infer from."""
    bad = []
    n = int(card.get("pages") or page_count or 0)
    if n <= 0:
        bad.append("pages is missing")
    read = set(card.get("pages_read") or [])
    missing = [p for p in range(1, n + 1) if p not in read]
    if missing:
        bad.append("pages not read: %s (read EVERY page before classifying)" % missing)
    for k in ("re_no", "title"):
        if not card.get(k):
            bad.append("missing " + k)
    feats = card.get("features") or {}
    for name in FEATURES:
        f = feats.get(name)
        if not isinstance(f, dict) or "value" not in f or not f.get("page"):
            bad.append("feature '%s' needs a value and the page it was seen on" % name)
        elif n and not (1 <= int(f["page"]) <= n):
            bad.append("feature '%s' cites page %s outside 1..%d" % (name, f["page"], n))
    call = card.get("ejw_ac_call")
    ev = card.get("ejw_ac_evidence") or []
    if call not in ("EJW", "AC", "UNKNOWN", "BOTH", "NOT_APPLICABLE"):
        bad.append("ejw_ac_call must be EJW, AC, BOTH, NOT_APPLICABLE or UNKNOWN")
    if call == "UNKNOWN" and len({e.get("page") for e in ev if e.get("page")}) < 2:
        bad.append("UNKNOWN is not accepted until clues were looked for on at least two different pages (list what each page showed in ejw_ac_evidence)")
    if call in ("EJW", "AC") and not any(e.get("call") == call for e in ev):
        bad.append("call %s needs at least one evidence entry pointing to it" % call)
    return bad


def infer(card):
    """(section_key, variant, trace). Rules from the price list sections and today's readings; the result is a SUGGESTION for the owner to confirm."""
    f = {k: (v or {}).get("value") for k, v in (card.get("features") or {}).items()}
    trace = []
    cores2 = (f.get("cores") or 1) >= 2
    if f.get("belt_drive"):
        key = "belt2" if cores2 else "belt"
        trace.append("belt drive seen -> %s" % SECTIONS[key])
    elif f.get("cowl"):
        key = "cowl2" if cores2 else "cowl"
        trace.append("cowl seen -> %s" % SECTIONS[key])
    elif f.get("orientation") == "horizontal":
        key = "horizontal"
        trace.append("horizontal orientation -> %s" % SECTIONS[key])
    else:
        key = "cooling"
        trace.append("no belt drive or cowl, vertical -> %s" % SECTIONS[key])
    variant = []
    call = card.get("ejw_ac_call")
    if key == "cooling":
        if call in ("EJW", "AC"):
            variant.append(call)
        pro_ejw = [n for n, c in (("cap/breather on the tank", f.get("cap_breather")), ("carbon steel tanks", f.get("tank_material") == "carbon"), ("coolant capacity noted", f.get("coolant_note"))) if c]
        pro_ac = [n for n, c in (("stainless tanks", f.get("tank_material") == "stainless"),) if c]
        trace.append("EJW clues: %s | AC clues: %s | called: %s" % (", ".join(pro_ejw) or "none", ", ".join(pro_ac) or "none", call))
    if f.get("core_guard"):
        variant.append("c/w core guard")
    return key, " ".join(variant), trace


def new_card(pdf, out):
    import pymupdf
    doc = pymupdf.open(pdf)
    d = os.path.splitext(out)[0] + "_pages"
    os.makedirs(d, exist_ok=True)
    for i, p in enumerate(doc, 1):
        p.get_pixmap(dpi=100 if p.rect.width > 900 else 110).save(os.path.join(d, "p%02d.png" % i))
    card = {"pdf": pdf, "pages": len(doc), "pages_read": [], "re_no": "", "title": "", "overall_w": None, "overall_h": None,
            "features": {n: {"value": None, "page": None} for n in FEATURES}, "ejw_ac_evidence": [], "ejw_ac_call": ""}
    json.dump(card, open(out, "w", encoding="utf-8"), indent=1)
    print("wrote %s and %d page images in %s: view EVERY page, then fill the card" % (out, len(doc), d))


def main(argv):
    if len(argv) < 2 or argv[0] not in ("new", "check"):
        print(__doc__)
        return 1
    if argv[0] == "new":
        new_card(argv[1], argv[2])
        return 0
    card = json.load(open(argv[1], encoding="utf-8"))
    bad = validate(card)
    for b in bad:
        print("PROBLEM:", b)
    if bad:
        return 2
    key, variant, trace = infer(card)
    print("suggested type: %s%s" % (SECTIONS[key], (" (" + variant + ")") if variant else ""))
    for t in trace:
        print("  -", t)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
