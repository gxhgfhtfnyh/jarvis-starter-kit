"""Writes J:\\Jarvis2\\logs\\gmail_digest.json = {"unread": N, "ts": ISO} for the Archive Terminal's INBOX WALL tile.

Read-only: asks Gmail only for the INBOX label's unread counter (no senders, subjects or bodies are fetched or stored).
    python gmail_digest.py          # write the digest, print it
Run on a schedule (see the 'Jarvis Gmail Digest' task) or by hand. A failure leaves the previous digest untouched."""
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OUT = Path(os.environ.get("GMAIL_DIGEST_FILE") or r"J:\Jarvis2\logs\gmail_digest.json")


def inbox_unread(service=None):
    if service is None:
        import gmail_api
        service = gmail_api.get_service()
    label = service.users().labels().get(userId="me", id="INBOX").execute()
    return int(label.get("messagesUnread", 0))


def write_digest(unread, path=OUT):
    data = {"unread": int(unread), "ts": datetime.now().isoformat(timespec="seconds")}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)
    return data


if __name__ == "__main__":
    try:
        print(write_digest(inbox_unread()))
    except Exception as e:  # keep the old digest; exit non-zero so a scheduled run shows as failed
        print(f"gmail digest failed: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(1)
