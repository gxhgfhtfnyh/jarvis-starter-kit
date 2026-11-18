# -*- coding: utf-8 -*-
"""
Item 16 of the 2026-08-09 tools brainstorm: saves Gmail attachments from a
given message to a designated local folder, using the real Gmail API
already wired up (gmail_api.py's existing OAuth token).

Usage:
    from attachment_saver import save_attachments
    save_attachments(message_id, dest_dir="~/Downloads/JarvisAttachments")
"""
import base64
import sys
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")

DEFAULT_DEST = Path.home() / "Downloads" / "JarvisAttachments"


def save_attachments(message_id, dest_dir=DEFAULT_DEST):
    from gmail_api import get_service
    service = get_service()
    msg = service.users().messages().get(userId="me", id=message_id).execute()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved = []

    def _walk(parts):
        for part in parts or []:
            filename = part.get("filename")
            body = part.get("body", {})
            if filename and body.get("attachmentId"):
                att = service.users().messages().attachments().get(
                    userId="me", messageId=message_id, id=body["attachmentId"]
                ).execute()
                data = base64.urlsafe_b64decode(att["data"])
                safe_name = Path(filename).name  # sender-supplied filename, never trust path segments ("..") in it
                out_path = (dest_dir / safe_name).resolve()
                if dest_dir.resolve() not in out_path.parents and out_path != dest_dir.resolve():
                    raise ValueError("attachment filename %r resolves outside %s, refusing to write" % (filename, dest_dir))
                if out_path.exists():
                    stem, suffix = out_path.stem, out_path.suffix
                    n = 1
                    while out_path.exists():
                        out_path = out_path.with_name("%s (%d)%s" % (stem, n, suffix))
                        n += 1
                out_path.write_bytes(data)
                saved.append(str(out_path))
            if part.get("parts"):
                _walk(part["parts"])

    _walk(msg.get("payload", {}).get("parts", []))
    return saved


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("Usage: python attachment_saver.py <message_id>")
        sys.exit(1)
    saved = save_attachments(sys.argv[1])
    print(f"Saved {len(saved)} attachment(s): {saved}" if saved else "No attachments found on that message.")
