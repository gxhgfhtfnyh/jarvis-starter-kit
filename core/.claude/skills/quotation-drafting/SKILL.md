---
name: quotation-drafting
description: "Use when asked to prepare a customer quotation from a technical drawing, for a business that prices off drawings (area-rate, unit-rate or similar). Always a draft."
---

# Quotation drafting (optional extra, off until you fill in your own prices)

Area/unit-rate quotation drafting from a technical drawing, built for a fabrication/engineering business. Scripts live in `scripts/quotation/`; nothing here sends anything, it only drafts.

1. **Read the drawing.** `type_card.py new <pdf> card.json` reads every page and records which clue came from which page; it refuses to guess a type ("UNKNOWN") unless two pages were actually checked. `size_check.py` double-checks width/height against the drawing's own dimension labels, trusting an explicit label over a nearby secondary line.
2. **Price it.** `price_list.py` and `rate_method.py` compute the price from your own rate table (you provide the real rates for your own business — none ship with this kit). Always area/unit x rate, rounded per your own rule.
3. **Draft it.** `make_quote.py spec.json out.pdf [--template old_quote.pdf]` builds the draft PDF, reusing the letterhead of a real past quotation if you give one; a diagonal DRAFT mark is always on until the owner approves the specific document. No signature or stamp goes on without that approval.
4. **Never send it.** Showing the owner the draft is always fine. Sending, emailing or submitting it needs the owner's yes on that exact PDF (the send gate covers this the same as any other external content).
5. `blind_test.py` is a sanity check: feed it a past real job and see if the computed price lands close to what was actually charged, before trusting the pipeline on a new one.

This is scaffolding, not your price book: `price_list.py`'s rate table starts empty. Fill in your own rates from your own price history before using this for a real quote.
