"""Tests for make_quote pure helpers. Run: python -m pytest -q test_make_quote.py (from the quotation folder)"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import make_quote as mq  # noqa: E402


@pytest.mark.parametrize("x,s", [(26850, "S$26,850.00"), (0, "S$0.00"), (12650.5, "S$12,650.50"), ("1000", "S$1,000.00")])
def test_money(x, s):
    assert mq.money(x) == s


@pytest.mark.parametrize("n,w", [
    (39300, "(S$:THIRTY NINE THOUSAND & THREE HUNDRED ONLY)"),     # the sample's own wording
    (24800, "(S$:TWENTY FOUR THOUSAND & EIGHT HUNDRED ONLY)"),
    (12650, "(S$:TWELVE THOUSAND SIX HUNDRED & FIFTY ONLY)"),
    (37150, "(S$:THIRTY SEVEN THOUSAND ONE HUNDRED & FIFTY ONLY)"),     # his real SDJ37/10 quote of 23/10/2026
    (137550, "(S$:ONE HUNDRED THIRTY SEVEN THOUSAND FIVE HUNDRED & FIFTY ONLY)"),
    (21001, "(S$:TWENTY ONE THOUSAND & ONE ONLY)"),
    (500, "(S$:FIVE HUNDRED ONLY)"),
    (1000, "(S$:ONE THOUSAND ONLY)"),
    (950, "(S$:NINE HUNDRED & FIFTY ONLY)"),
    (19, "(S$:NINETEEN ONLY)"),
    (0, "(S$:ZERO ONLY)"),
])
def test_words(n, w):
    assert mq.words(n) == w


@pytest.mark.parametrize("l,r,w,dx", [(100, 300, 600, 100), (0, 600, 600, 0), (350, 550, 600, -150)])
def test_shift_to_center(l, r, w, dx):
    assert mq.shift_to_center(l, r, w) == dx


def test_letterhead_pieces_centre_the_name_and_stay_inside_the_page():
    import glob
    fitz = pytest.importorskip("fitz")
    tpl = glob.glob(r"J:\Jarvis2\logs\discord_media\*SDJRE1717*.pdf")
    if not tpl:
        pytest.skip("template quote pdf not present")
    page = fitz.open(tpl[0])[0]
    pieces = mq.letterhead_pieces(page, fitz)
    assert len(pieces) == 3
    name_rect, _ = pieces[0]
    pix = pieces[0][1]
    span = mq._content_span(pix)
    left = name_rect.x0 + span[0] / (300 / 72.0)
    right = name_rect.x0 + span[1] / (300 / 72.0)
    assert abs((left + right) / 2 - page.rect.width / 2) < 1.5      # name block centred on the page
    for rect, _ in pieces:
        assert rect.x0 >= -0.5 and rect.x1 <= page.rect.width + 60   # crop boxes may reach the margin; content itself is checked below
    logo_rect, logo_pix = pieces[2]
    lspan = mq._content_span(logo_pix)
    assert logo_rect.x0 + lspan[1] / (300 / 72.0) <= page.rect.width - 20   # logos keep a right margin


def test_total_of():
    assert mq.total_of([{"price": 12650}, {"price": "12150"}]) == 24800.0


GOOD = {"date": "07/09/2026", "customer": ["X Sdn Bhd"], "subject": "RE1476 Cooling Sections", "scope": "To supply & fabricate RE1476 cooling sections as per drawings.",
        "rows": [{"lines": ["RE1476 EJW Cooling Section", "Model: x"], "qty": "1 pc", "price": 12650}], "terms": ["TERMS OF PAYMENT : 60 DAYS"]}


def test_good_spec_has_no_problems():
    assert mq.validate_spec(GOOD) == []


def test_missing_fields_reported():
    assert "missing terms" in mq.validate_spec(dict(GOOD, terms=[]))
    assert "missing customer" in mq.validate_spec({k: v for k, v in GOOD.items() if k != "customer"})


@pytest.mark.parametrize("d", ["2026-09-07", "7/9/2026", "07/09/26", ""])
def test_date_must_be_one_dd_mm_yyyy(d):
    assert any("date" in p for p in mq.validate_spec(dict(GOOD, date=d)))


def test_overwide_description_line_is_rejected():
    pytest.importorskip("fitz")
    wide = {"lines": ["RE1476 x", "Steel parts fabrication & assembly including consumables,"], "qty": "1 pc", "price": 1}
    assert any("too wide" in p for p in mq.validate_spec(dict(GOOD, rows=[wide])))
    ok = {"lines": ["RE1476 x", "Steel parts fabrication & assembly including"], "qty": "1 pc", "price": 1}
    assert not any("too wide" in p for p in mq.validate_spec(dict(GOOD, rows=[ok])))


def test_row_needs_lines_qty_price():
    assert any("row 1" in p for p in mq.validate_spec(dict(GOOD, rows=[{"lines": ["x"], "qty": "1"}])))


def test_catches_subject_re_number_that_the_body_never_mentions():
    bad = dict(GOOD, subject="RE1357 Cooling Sections")       # the sample quotation's own mistake (subject RE1357, body RE1958)
    p = mq.validate_spec(bad)
    assert any("RE1357" in x for x in p)


def test_smoke_build_creates_a_pdf_with_the_total_and_no_signature_text(tmp_path):
    import glob
    fitz = pytest.importorskip("fitz")
    tpl = glob.glob(r"J:\Jarvis2\logs\discord_media\*SDJRE1717*.pdf")
    if not tpl:
        pytest.skip("template quote pdf not present")
    out = tmp_path / "q.pdf"
    mq.build(GOOD, str(out), tpl[0])
    text = "".join(p.get_text() for p in fitz.open(str(out)))
    assert "S$12,650.00" in text and "TOTAL" in text and "07/09/2026" in text
    assert "DRAFT" in text
    assert text.upper().count("MR. ARIC KUM") == 1            # the printed name is drawn once (approved in the chat); the old copy from the template is wiped
    page = fitz.open(str(out))[0]
    low = [r for im in page.get_images(full=True) for r in page.get_image_rects(im[0]) if r.y0 > 200]
    assert low == []                                          # no signature or stamp image anywhere below the letterhead
    assert text.count("07/09/2026") == 1                      # one date only


def test_printed_size_needs_a_source():
    row = {"lines": ["RE1 x", "Overall size 1000mm(W) x 2000mm(H)"], "qty": "1 pc", "price": 1}
    assert any("size_source" in p for p in mq.validate_spec(dict(GOOD, rows=[row])))
    assert not any("size_source" in p for p in mq.validate_spec(dict(GOOD, rows=[dict(row, size_source="drawing")])))
    assert any("size_source" in p for p in mq.validate_spec(dict(GOOD, rows=[dict(row, size_source="guess")])))


SIG_PDF = r"J:\Jarvis2\logs\discord_media\1793247006_1565196630_dummy"          # replaced below by a glob of the real SDJ1357 quote


def _real_pdfs():
    import glob
    tpl = glob.glob(r"J:\Jarvis2\logs\discord_media\*SDJRE1717*.pdf")
    sig = glob.glob(r"J:\Jarvis2\logs\discord_media\*SDJ1357*.pdf")
    return (tpl[0] if tpl else None), (sig[0] if sig else None)


def test_signature_without_a_recorded_approval_is_rejected():
    p = mq.validate_spec(dict(GOOD, signature={"from": "x.pdf"}))
    assert any("approved_by" in x for x in p)
    p = mq.validate_spec(dict(GOOD, signature={"from": "x.pdf", "approved_by": "ok"}))
    assert any("approved_by" in x for x in p)                      # too short to be a real record
    assert not any("approved_by" in x for x in mq.validate_spec(dict(GOOD, signature={"from": "x.pdf", "approved_by": "Approver, chat message 1000000000000000001"})))


def test_final_page_has_signature_art_and_no_draft_mark(tmp_path):
    fitz = pytest.importorskip("fitz")
    tpl, sig = _real_pdfs()
    if not tpl or not sig:
        pytest.skip("real quotation pdfs not present")
    spec = dict(GOOD, draft=False, signature={"from": sig, "approved_by": "Approver, chat message 1000000000000000001"})
    out = tmp_path / "final.pdf"
    mq.build(spec, str(out), tpl)
    page = fitz.open(str(out))[0]
    low = [r for im in page.get_images(full=True) for r in page.get_image_rects(im[0]) if r.y0 > 200]
    assert len(low) == 1                                           # exactly one signature-and-stamp image below the letterhead
    text = page.get_text()
    assert "DRAFT" not in text and text.upper().count("MR. ARIC KUM") == 0      # the name is inside the signature art, so no second typed copy


def test_build_does_not_leak_a_multi_page_templates_own_drawings(tmp_path):
    # Real bug found live (2026-11-10): a `template` is often a real
    # FINISHED quote with that job's own customer drawings on pages 2+,
    # not a bare 1-page letterhead - build() only ever touched page 0, so
    # those old pages silently rode along into the output ahead of the
    # new drawings. Caught via a real page-count mismatch (16-page SDJ
    # template produced a 17-page PREPTECH quote instead of 2), not
    # guessed. A multi-page template must contribute ONLY its letterhead.
    import glob
    fitz = pytest.importorskip("fitz")
    multi_page_tpl = glob.glob(r"J:\Jarvis2\logs\quotation_data\Quotation_RE1476_SDJ.pdf")
    one_drawing = glob.glob(r"J:\Jarvis2\logs\discord_media\*SDJRE1717*.pdf")
    if not multi_page_tpl or not one_drawing:
        pytest.skip("real multi-page template or a real drawing pdf not present")
    assert len(fitz.open(multi_page_tpl[0])) > 1, "this test needs a genuinely multi-page template to prove the fix"
    out = tmp_path / "q.pdf"
    spec = dict(GOOD, drawings=[one_drawing[0]])
    mq.build(spec, str(out), multi_page_tpl[0])
    result = fitz.open(str(out))
    assert len(result) == 2, f"expected 1 letterhead page + 1 appended drawing, got {len(result)} pages - the template's own old pages leaked through"


def test_default_build_still_has_the_draft_mark_and_no_signature(tmp_path):
    fitz = pytest.importorskip("fitz")
    tpl, _ = _real_pdfs()
    if not tpl:
        pytest.skip("template quote pdf not present")
    out = tmp_path / "d.pdf"
    mq.build(GOOD, str(out), tpl)
    page = fitz.open(str(out))[0]
    assert "DRAFT" in page.get_text()
    assert [r for im in page.get_images(full=True) for r in page.get_image_rects(im[0]) if r.y0 > 200] == []
