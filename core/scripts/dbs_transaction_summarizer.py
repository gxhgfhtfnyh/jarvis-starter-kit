# -*- coding: utf-8 -*-
"""
Parses DBS/POSB transaction alert emails into a running ledger vault note.

Real finding (2026-08-08): DBS never attaches an actual PDF statement to any
notification email - "Your eDocument(s) are ready for viewing" is a pure
log-in-to-view prompt with zero attachment. The only genuinely parseable data
lives in the two real alert types confirmed live against this inbox:
  - "digibank Alerts - You've received a transfer" (incoming PayNow)
  - "iBanking Alerts" (outgoing PayNow / other confirmed transactions)
Anything that doesn't match one of these two known body formats is skipped,
not guessed at - a wrong parse is worse than a missed one for financial data.

Usage:
    python dbs_transaction_summarizer.py [--max N]
"""
import base64
import re
import sys
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")

from gmail_api import get_service, list_unread

LEDGER_PATH = Path(r"J:\Jarvis-Brain\00 - Inbox\DBS Transaction Ledger.md")

RECEIVED_RE = re.compile(
    r"You have received SGD\s*([\d,]+\.\d{2}) via PayNow on (\d{2} \w{3} \d{4} \d{2}:\d{2}) SGT\.\s*"
    r"From:\s*([^\n]+?)\s*To:\s*(.+?account ending \d{4})",
    re.DOTALL,
)
SENT_RE = re.compile(
    r"Amount:\s*SGD\s*([\d,]+\.\d{2})\s*From:\s*(.+?ending \d{4})\s*To:\s*([^(]+)",
    re.DOTALL,
)


def _clean_html(html):
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub("<[^<]+?>", " ", html)
    text = re.sub(r"&nbsp;|&zwnj;", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _get_html_body(service, message_id):
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute()

    def find_html(payload):
        if payload.get("mimeType") == "text/html" and "data" in payload.get("body", {}):
            return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
        for part in payload.get("parts", []):
            found = find_html(part)
            if found:
                return found
        return ""

    return find_html(msg["payload"])


def parse_transaction(text, subject, date):
    m = RECEIVED_RE.search(text)
    if m:
        amount, when, sender, account = m.groups()
        return {"direction": "received", "amount": amount, "when": when.strip(),
                "counterparty": sender.strip(), "account": account.strip(), "date": date}

    m = SENT_RE.search(text)
    if m and "received" not in text.lower()[:50]:
        amount, account, recipient = m.groups()
        return {"direction": "sent", "amount": amount, "when": date,
                "counterparty": recipient.strip(), "account": account.strip(), "date": date}

    return None


def collect_transactions(max_results=50):
    service = get_service()
    messages = list_unread(
        max_results=max_results,
        query='(from:dbs.com OR from:posb.com.sg) (subject:"received a transfer" OR subject:"iBanking Alerts")',
    )
    transactions, unparsed = [], []
    for m in messages:
        html = _get_html_body(service, m["id"])
        text = _clean_html(html)
        parsed = parse_transaction(text, m.get("subject", ""), m.get("date", ""))
        if parsed:
            parsed["id"] = m["id"]
            transactions.append(parsed)
        else:
            unparsed.append(m.get("subject", ""))
    return transactions, unparsed


def append_to_ledger(transactions):
    if not LEDGER_PATH.exists():
        LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
        LEDGER_PATH.write_text(
            "---\nstatus: active\nproject: personal\ntype: log\nsensitivity: financial\n---\n"
            "# DBS Transaction Ledger\n\n"
            "Parsed from real DBS/POSB alert emails (`dbs_transaction_summarizer.py`). "
            "Only covers what's actually emailed - incoming/outgoing PayNow alerts. "
            "Not a full statement; DBS never attaches real statements to email.\n\n"
            "| Date | Direction | Amount (SGD) | Counterparty | Account |\n"
            "|---|---|---|---|---|\n",
            encoding="utf-8",
        )

    existing = LEDGER_PATH.read_text(encoding="utf-8")
    existing_ids = set(re.findall(r"<!--id:(\w+)-->", existing))

    new_rows = []
    for t in transactions:
        if t["id"] in existing_ids:
            continue
        new_rows.append(
            f"| {t['when']} | {t['direction']} | {t['amount']} | {t['counterparty']} | {t['account']} |"
            f"<!--id:{t['id']}-->"
        )

    if new_rows:
        LEDGER_PATH.write_text(existing + "\n".join(new_rows) + "\n", encoding="utf-8")
    return len(new_rows)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    max_results = 50
    if "--max" in sys.argv:
        max_results = int(sys.argv[sys.argv.index("--max") + 1])

    transactions, unparsed = collect_transactions(max_results=max_results)
    added = append_to_ledger(transactions)
    print(f"Parsed {len(transactions)} transactions, {added} new (added to ledger).")
    if unparsed:
        print(f"{len(unparsed)} DBS emails matched the search but didn't match a known format "
              f"(not guessed at): {unparsed[:5]}")
