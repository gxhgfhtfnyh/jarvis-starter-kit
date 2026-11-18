# -*- coding: utf-8 -*-
"""Parse the company's unit price list (xlsx, standard library only) into clean records, for the quotation pipeline (2026-10-29).
    python price_list.py <price_list.xlsx> [--out prices.json]       # prints a summary; --out writes the records as JSON
Each record: {section, job, re_no, description, unit_price, price_up8, benchmark, w_mm, h_mm, area_m2, rate_per_m2, tags}
  job     leading J-number in the description (e.g. J11031964); re_no = last four digits (RE1964), which is how drawings are named
  tags    words found in the description: ejw, ac, core_guard, exclude_core, ht, double, belt_driven, cowling, horizontal, tank
Business data stays local: this reads a file you give it and writes only where you point --out. Pure helpers are unit tested."""
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
TAGS = [("ejw", r"\bejw\b"), ("ac", r"\bac\b"), ("core_guard", r"core guard"), ("exclude_core", r"exclude core"), ("ht", r"\(ht\)|\bht\b"),
        ("double", r"double"), ("belt_driven", r"belt driven"), ("cowling", r"cowling"), ("horizontal", r"horizontal"), ("tank", r"exp tank|expansion tank")]


def parse_wh(text):
    """'2646 x 2864' -> (2646, 2864); None when it does not look like a size."""
    m = re.match(r"^\s*(\d{3,5})\s*[xX*]\s*(\d{3,5})\s*$", str(text or ""))
    return (int(m.group(1)), int(m.group(2))) if m else None


def job_ref(description):
    """Leading J-number of a description and its RE number (last four digits): 'J11031964 EJW ...' -> ('J11031964', 'RE1964')."""
    m = re.match(r"^\s*(J\d{6,9})\b", str(description or ""), re.I)
    return (m.group(1).upper(), "RE" + m.group(1)[-4:]) if m else (None, None)


def tags_of(description):
    d = str(description or "").lower()
    return [name for name, rx in TAGS if re.search(rx, d)]


def to_float(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def read_rows(path):
    """All non-empty rows of the first sheet as lists of strings/None (shared strings resolved)."""
    z = zipfile.ZipFile(path)
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter("{%s}t" % NS["m"])) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", NS)]
    rows = []
    for r in ET.fromstring(z.read("xl/worksheets/sheet1.xml")).iter("{%s}row" % NS["m"]):
        vals = []
        for c in r.findall("m:c", NS):
            v = c.find("m:v", NS)
            vals.append(None if v is None else (shared[int(v.text)] if c.get("t") == "s" else v.text))
        if any(x not in (None, "") for x in vals):
            rows.append(vals)
    return rows


def records(rows):
    """Turn raw rows into price records. A row with a model text but no price and no size starts a new section."""
    out, section = [], ""
    for r in rows:
        r = list(r) + [None] * (8 - len(r))
        desc, price = r[1], to_float(r[2])
        wh = parse_wh(r[5])
        if desc and price is None and wh is None:
            if job_ref(desc)[0] is None:     # a real heading; a priceless J-number row (e.g. 'J2101357 EJW Cooling Section') is just an unpriced job
                section = str(desc).strip()
            continue
        if not desc or price is None:
            continue
        job, re_no = job_ref(desc)
        w, h = wh if wh else (None, None)
        area = to_float(r[6]) or (w * h / 1e6 if w and h else None)
        out.append({"section": section, "job": job, "re_no": re_no, "description": str(desc).strip(), "unit_price": price, "price_up8": to_float(r[3]),
                    "benchmark": (r[4] or None), "w_mm": w, "h_mm": h, "area_m2": area,
                    "rate_per_m2": round(price / area, 1) if area else None, "tags": tags_of(desc)})
    return out


def main(argv):
    if not argv:
        print(__doc__)
        return 1
    recs = records(read_rows(argv[0]))
    secs = {}
    for r in recs:
        secs.setdefault(r["section"], []).append(r)
    print("%d priced records in %d sections" % (len(recs), len(secs)))
    for s, rs in secs.items():
        rates = [x["rate_per_m2"] for x in rs if x["rate_per_m2"]]
        print("  %-50s %3d records%s" % (s[:50], len(rs), ("  rate/m2 %d..%d" % (min(rates), max(rates))) if rates else ""))
    if "--out" in argv:
        open(argv[argv.index("--out") + 1], "w", encoding="utf-8").write(json.dumps(recs, indent=1))
        print("wrote", argv[argv.index("--out") + 1])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
