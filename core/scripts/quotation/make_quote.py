# -*- coding: utf-8 -*-
"""Draft quotation maker. ALWAYS a draft; nothing is sent anywhere.
    python make_quote.py spec.json out.pdf [--template old_quote.pdf]
Page 1 reuses the letterhead of a real earlier quotation PDF (the template): everything under the letterhead (body, signature, stamp) is redacted,
then the date, reference, customer block, subject, item table, total in words, terms and closing are drawn from the spec. A diagonal DRAFT mark is
drawn. Signature and company stamp are NEVER drawn. Page 2+: the customer's drawing PDFs listed in spec["drawings"] are appended.
spec.json: {"date": "dd/mm/yyyy" (ONE date, above the date line), "ref": "...", "customer": ["line", ...], "attn": "Name",
            "subject": "RE1476 EJW & CAC Cooling Sections", "scope": "To supply & fabricate ... as per Drawings given.",
            "rows": [{"lines": ["bold title", "line", ...], "qty": "1 pc", "price": 12650, "bold_first": true}, ...],
            "terms": ["TERMS OF PAYMENT : ...", "TERMS OF VALIDITY : ..."], "drawings": ["path.pdf", ...]}
Total = sum of row prices (qty is text, rows are single units as in his sample). Pure helpers (money, words, total, validate_spec) are unit tested."""
import json
import re
import sys

ONES = ["", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE", "THIRTEEN", "FOURTEEN", "FIFTEEN",
        "SIXTEEN", "SEVENTEEN", "EIGHTEEN", "NINETEEN"]
TENS = ["", "", "TWENTY", "THIRTY", "FORTY", "FIFTY", "SIXTY", "SEVENTY", "EIGHTY", "NINETY"]


def money(x):
    """26850 -> 'S$26,850.00' (the approved chat format writes S$)"""
    return "S${:,.2f}".format(float(x))


def _tens_ones(n):
    """1..99 in capitals."""
    return TENS[n // 10] + ((" " + ONES[n % 10]) if n % 10 else "") if n >= 20 else ONES[n]


def _chunks(n):
    """Words of n as chunks, in reading order: millions/thousands as 'X THOUSAND', then the hundreds, then the tens and ones."""
    chunks = []
    for unit, name in ((1_000_000, "MILLION"), (1000, "THOUSAND")):
        g, n = divmod(n, unit)
        if g:
            hund, rest = divmod(g, 100)
            part = ((ONES[hund] + " HUNDRED ") if hund else "") + (_tens_ones(rest) if rest else "")
            chunks.append(part.strip() + " " + name)
    hund, rest = divmod(n, 100)
    if hund:
        chunks.append(ONES[hund] + " HUNDRED")
    if rest:
        chunks.append(_tens_ones(rest))
    return chunks


def words(amount):
    """Whole dollars in capitals in his style. ONE '&', placed before the last chunk when something comes before it. His real quotes:
    39300 -> '(S$:THIRTY NINE THOUSAND & THREE HUNDRED ONLY)', 37150 -> '(S$:THIRTY SEVEN THOUSAND ONE HUNDRED & FIFTY ONLY)'."""
    n = int(round(float(amount)))
    if n <= 0:
        return "(S$:ZERO ONLY)"
    ch = _chunks(n)
    text = " ".join(ch[:-1]) + (" & " if len(ch) > 1 else "") + ch[-1]
    return "(S$:%s ONLY)" % text


def total_of(rows):
    return sum(float(r["price"]) for r in rows)


SIZE_SOURCES = {"drawing", "owner", "sheet"}   # where a printed width x height came from: read off this drawing, told by the owner, or taken from the price sheet
DESC_MAX_PT = 228      # description column runs x 92..324 on the page; leave a little air before the QTY line


def line_width(text, bold=False, size=9.5):
    import fitz
    return fitz.get_text_length(text, fontname="hebo" if bold else "helv", fontsize=size)


def validate_spec(spec):
    """List of problems; empty when the spec can be drawn. Also catches the sample's own error: a subject RE number that never appears in the scope line."""
    bad = []
    for k in ("date", "customer", "subject", "scope", "rows", "terms"):
        if not spec.get(k):
            bad.append("missing " + k)
    if spec.get("date") and not re.match(r"^\d{2}/\d{2}/\d{4}$", spec["date"]):
        bad.append("date must be dd/mm/yyyy (one date only)")
    sig = spec.get("signature")
    if sig and (not isinstance(sig, dict) or not sig.get("from") or len(str(sig.get("approved_by", ""))) < 10):
        bad.append("signature needs {'from': <real quotation pdf>, 'approved_by': <who approved it and when, e.g. the Discord message id>}: no signature without a recorded approval")
    for i, r in enumerate(spec.get("rows") or []):
        if not r.get("lines") or "price" not in r or not r.get("qty"):
            bad.append("row %d needs lines, qty and price" % (i + 1))
        if any("overall size" in str(ln).lower() for ln in r.get("lines") or []) and r.get("size_source") not in SIZE_SOURCES:
            bad.append("row %d prints a size but says no source: set size_source to one of %s (2026-10-29: a size from the wrong drawing went into a draft)" % (i + 1, sorted(SIZE_SOURCES)))
        for k, ln in enumerate(r.get("lines") or []):
            if line_width(ln, bold=(k == 0 and r.get("bold_first", True))) > DESC_MAX_PT:
                bad.append("row %d line %d is too wide for the description column (would run over QTY): %r" % (i + 1, k + 1, ln[:40]))
    def norm(s):
        return re.sub(r"[\s:]", "", s).upper()          # 'RE:1357', 'Re 1357' and 'RE1357' are the same number
    res = set(norm(m) for m in re.findall(r"RE[\s:]?\d{3,4}", str(spec.get("subject", "")), re.I))
    body = norm(str(spec.get("scope", "")) + " ".join(l for r in spec.get("rows") or [] for l in r.get("lines", [])))
    for re_no in res:
        if re_no not in body:
            bad.append("subject mentions %s but the scope/description never does (check for a wrong RE number)" % re_no)
    return bad


def signature_pixmap(pdf_path, fitz):
    """(pixmap with transparency, width_pt, height_pt) of the signature-and-stamp image on page 1 of a real quotation PDF (the image placed below the
    letterhead, y0 > 200). The artwork is copied byte for byte from his father's own quotation; nothing is drawn or traced."""
    doc = fitz.open(pdf_path)
    page = doc[0]
    for im in page.get_images(full=True):
        for r in page.get_image_rects(im[0]):
            if r.y0 > 200:
                pix = fitz.Pixmap(doc, im[0])
                if im[1]:                                   # soft mask = the transparency of the stamp and signature
                    pix = fitz.Pixmap(pix, fitz.Pixmap(doc, im[1]))
                return pix, r.width, r.height
    raise ValueError("no signature image found below the letterhead in %s" % pdf_path)


def shift_to_center(left, right, page_w):
    """Horizontal shift (pt) that puts content spanning left..right in the middle of the page."""
    return page_w / 2.0 - (left + right) / 2.0


def _content_span(pix, thresh=235):
    """(first, last) x pixel columns holding non-white pixels, or None when the piece is blank."""
    n, w = pix.n, pix.width
    s = pix.samples
    cols = [x for x in range(w) if any(min(s[(y * w + x) * n:(y * w + x) * n + min(n, 3)]) < thresh for y in range(0, pix.height, 2))]
    return (cols[0], cols[-1]) if cols else None


def letterhead_pieces(page, fitz, dpi=300):
    """[(target_rect, pixmap)] for the template letterhead, re-placed so the company name and the address block are centred on the page
    (the original image is lopsided: name block sits left of centre, certification logos right). Logos keep their place, moved right only
    enough to clear the address. Everything is cropped from the template at 300 dpi, so nothing is redrawn or invented."""
    W = page.rect.width
    zoom = dpi / 72.0
    # the address piece reaches down to 143 pt so the small 'Reg. No.' line under the GST line is whole (his real quote shows it); the logo piece stays at 131
    cuts = {"name": fitz.Rect(0, 0, W, 74), "addr": fitz.Rect(0, 74, 358, 143), "logo": fitz.Rect(358, 74, W, 131)}
    out, addr_right = [], None
    for key in ("name", "addr", "logo"):
        clip = cuts[key]
        pix = page.get_pixmap(dpi=dpi, clip=clip)
        span = _content_span(pix)
        if not span:
            continue
        left, right = clip.x0 + span[0] / zoom, clip.x0 + span[1] / zoom
        dx = shift_to_center(left, right, W) if key != "logo" else 0.0
        if key == "addr":
            addr_right = right + dx
        if key == "logo" and addr_right is not None and left < addr_right + 10:
            dx = addr_right + 10 - left                       # clear the shifted address; stays inside the right margin (checked by a test)
        out.append((fitz.Rect(clip.x0 + dx, clip.y0, clip.x1 + dx, clip.y1), pix))
    return out


def build(spec, out_path, template):
    import fitz   # PyMuPDF
    doc = fitz.open(template)
    # Real bug found live (2026-11-10): only page 0 is ever touched below,
    # but a `template` PDF is routinely a real FINISHED quote with that
    # job's own customer drawings on pages 2+ (not a bare 1-page
    # letterhead). Leaving those pages in `doc` meant they silently rode
    # along into the output, ahead of the new drawings appended at the
    # end - a different, unrelated customer's drawings ending up in a new
    # quote. Caught via a real page-count check (16-page SDJ template ->
    # 17-page PREPTECH output instead of the expected 2), not guessed.
    while len(doc) > 1:
        doc.delete_page(1)
    page = doc[0]
    W, H = page.rect.width, page.rect.height
    pieces = letterhead_pieces(page, fitz)                              # cut the old letterhead into name / address / logos before wiping the page
    page.add_redact_annot(fitz.Rect(0, 0, W, H), fill=(1, 1, 1))        # wipe everything: old letterhead image, old date, signature and stamp
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_PIXELS if hasattr(fitz, "PDF_REDACT_IMAGE_PIXELS") else 2)
    page.draw_rect(fitz.Rect(0, 0, W, H), color=None, fill=(1, 1, 1))   # in case image pixels were only partly blanked
    for rect, pix in pieces:
        page.insert_image(rect, pixmap=pix)
    f, fb = "helv", "hebo"
    # diagonal DRAFT mark, drawn first so every line of text sits on top of it
    if spec.get("draft", True):      # the owner can ask for the final, unmarked page ("do not put draft watermark", 2026-10-29)
        page.insert_text((110, 600), "DRAFT - NOT ISSUED", fontsize=46, fontname=fb, color=(0.9, 0.9, 0.9), morph=(fitz.Point(110, 600), fitz.Matrix(1, 0, 0, 1, 0, 0).prerotate(35)))
    page.insert_text((470, 139), spec["date"], fontsize=10, fontname=f)        # ONE date, above the date line
    page.draw_line((402, 146), (520, 146), width=0.6)
    page.insert_text((372, 148), "Date:", fontsize=10, fontname=f)
    y = 167       # block spacing follows his real SDJ37/10 quote: ref, customer lines, then Tel / Attn / salutation a little looser
    page.insert_text((56, y), "Our Ref. No : %s" % (spec.get("ref") or "(to be assigned)"), fontsize=10, fontname=f)
    y += 13
    for ln in spec["customer"]:
        page.insert_text((56, y), ln, fontsize=10, fontname=f)
        y += 13
    for key, fmt in (("tel", "Tel : %s"), ("attn", "Attn : %s,"), ("salutation", "%s")):
        if spec.get(key):
            y += 2.5
            page.insert_text((56, y), fmt % spec[key], fontsize=10, fontname=f)
            y += 13
    y += 12
    page.insert_text((W / 2 - 38, y), "QUOTATION", fontsize=12, fontname=fb)
    y += 20
    page.insert_text((56, y), "SUBJECT :- " + spec["subject"], fontsize=10.5, fontname=fb)
    page.draw_line((56, y + 2), (56 + 6.1 * len("SUBJECT :- " + spec["subject"]), y + 2), width=0.7)
    y += 13
    page.insert_text((56, y), "In response to your verbal enquiry capturing the above subject, we are pleased to", fontsize=10, fontname=f)
    page.insert_text((56, y + 12), "submit our quotation as follows :-", fontsize=10, fontname=f)
    y += 28
    page.insert_text((56, y), spec["scope"], fontsize=10, fontname=f)
    y += 10
    x = [54, 88, 324, 389, 462, 550]
    top = y
    page.draw_rect(fitz.Rect(x[0], top, x[5], top + 14), width=0.8)
    for xi, name in zip((60, 188, 346, 404, 492), ("ITEM", "DESCRIPTION", "QTY", "U PRICE", "TOTAL")):
        page.insert_text((xi, top + 10.5), name, fontsize=9.5, fontname=f)
    y = top + 14
    start = y
    for n_item, r in enumerate(spec["rows"], 1):
        page.insert_text((68, y + 12), str(n_item), fontsize=9.5, fontname=f)       # ITEM number, as in his real quote
        ly = y + 12
        for k, ln in enumerate(r["lines"]):
            page.insert_text((92, ly), ln, fontsize=9.5, fontname=fb if (k == 0 and r.get("bold_first", True)) else f)
            ly += 10.8
        page.insert_text((335, y + 12), r["qty"], fontsize=9.5, fontname=fb)
        page.insert_text((394, y + 12), money(r["price"]), fontsize=9.5, fontname=fb)
        page.insert_text((467, y + 12), money(r["price"]), fontsize=9.5, fontname=fb)
        y = max(ly, y + 36) + 2
    end = max(y, start + 150)
    for xi in x:
        page.draw_line((xi, top), (xi, end), width=0.8)
    page.draw_line((x[0], end), (x[5], end), width=0.8)
    tot = total_of(spec["rows"])
    page.draw_rect(fitz.Rect(x[4], end, x[5], end + 15), width=0.9)
    w_txt = words(tot)
    # his real SDJ37/10 quote: the amount in words sits in the total row on the left and 'TOTAL :' (bold, larger) just before the amount box;
    # an amount in words too long for that gap (over 330 pt) goes on its own line under the table instead
    in_row = line_width(w_txt, size=9) <= 330
    page.insert_text((58, end + (11 if in_row else 26)), w_txt, fontsize=9, fontname=f)
    page.insert_text((396, end + 12), "TOTAL :", fontsize=11, fontname=fb)
    page.insert_text((467, end + 11.5), money(tot), fontsize=10, fontname=fb)
    y = end + (36 if in_row else 50)
    for ln in spec["terms"]:
        page.insert_text((56, y), ln, fontsize=10, fontname=f)
        y += 15
    y += 6
    page.insert_text((56, y), "We hope our quotation meets with your requirement & look forward to hearing from", fontsize=10, fontname=f)
    page.insert_text((56, y + 12), "you soon. Thank You.", fontsize=10, fontname=f)
    if y + 38 > H - 110:     # the sign-off needs room under it for the owner's signature and stamp; refuse rather than let it get cut off
        raise ValueError("too many items for page 1: sign-off would sit at %.0f of %.0f pt" % (y + 38, H))
    page.insert_text((56, y + 38), "Yours faithfully,", fontsize=10, fontname=f)
    sig = spec.get("signature")
    if sig:       # signature + stamp image copied from a real quotation PDF the owner points at, only with an approval note (see validate_spec)
        pix, w_pt, h_pt = signature_pixmap(sig["from"], fitz)
        page.insert_image(fitz.Rect(63, y + 46, 63 + w_pt, y + 46 + h_pt), pixmap=pix)     # the art already includes the printed name 'MR. ARIC KUM'
    else:
        page.insert_text((56, min(y + 38 + 73, H - 36)), "MR. ARIC KUM", fontsize=10, fontname=f)    # without the artwork: the printed name only
    for d in spec.get("drawings") or []:
        src = fitz.open(d)
        doc.insert_pdf(src)
    doc.save(out_path)
    return out_path


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    spec = json.load(open(argv[0], encoding="utf-8"))
    problems = validate_spec(spec)
    if problems:
        print("spec problems:\n  " + "\n  ".join(problems))
        return 2
    template = argv[argv.index("--template") + 1] if "--template" in argv else None
    if not template:
        print("need --template <an earlier quotation pdf whose letterhead is reused>")
        return 2
    print("wrote", build(spec, argv[1], template), "| total", money(total_of(spec["rows"])), words(total_of(spec["rows"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
