# -*- coding: utf-8 -*-
"""Sets the Loom/Jarvis Discord bot account's "About Me" bio via a direct
REST call. Standing permission from the owner, 2026-08-16: "feel free to
update this regularly or whenever u like just dont reaveal private or
sensitive things" -- made a real reusable script rather than a throwaway
one-off since it's meant to be used again, not just this once.

Usage:
    python discord_set_bio.py "<bio text>"
    python discord_set_bio.py --stdin   (pipe via heredoc -- safer for text with embedded quotes)
"""
import sys
sys.path.insert(0, r"J:\Jarvis2\scripts")
from jarvis_keyring import get_password as keyring_get_password
import requests

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--stdin":
        bio = sys.stdin.read().rstrip("\n")
    else:
        bio = sys.argv[1]
    token = keyring_get_password("discord", "bot_token")
    # Real gotcha found live: PATCH /users/@me's "bio" field is a no-op for
    # bot accounts (200 OK, but the value silently doesn't take -- confirmed
    # by reading the response back, not assumed from the status code alone).
    # A bot's real "About Me" is the underlying Application's "description"
    # field instead, at a completely different endpoint.
    resp = requests.patch(
        "https://discord.com/api/v10/applications/@me",
        headers={"Authorization": f"Bot {token}"},
        json={"description": bio},
    )
    print(resp.status_code)
    print(resp.text[:500])
