# -*- coding: utf-8 -*-
"""
One-time interactive Spotify Web API authorization (Authorization Code
Flow). Run this once after the owner has registered a Spotify developer app
(developer.spotify.com/dashboard) and stored its client_id/client_secret in
keyring - this exchanges a real user login+consent for a long-lived refresh
token, which spotify_api.py then uses hands-off from then on.

Requires (the owner's own one-time setup, not through Claude):
    python -m keyring set spotify client_id
    python -m keyring set spotify client_secret
The app's Redirect URI (in the Spotify dashboard) must be exactly:
    http://127.0.0.1:8888/callback

Usage:
    python spotify_auth.py
"""
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import keyring
import requests

from jarvis_keyring import get_password

REDIRECT_URI = "http://127.0.0.1:8888/callback"
AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
SCOPES = " ".join([
    "user-read-playback-state",
    "user-modify-playback-state",
    "user-read-currently-playing",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-public",
    "playlist-modify-private",
    "user-top-read",  # top artists/tracks stats -- added 2026-08-16, the owner asked directly
    "user-read-recently-played",  # listening history -- added 2026-08-16, the owner asked directly
])

_result = {}


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        _result["code"] = qs.get("code", [None])[0]
        _result["state"] = qs.get("state", [None])[0]
        _result["error"] = qs.get("error", [None])[0]

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if _result["error"]:
            body = f"<html><body>Authorization failed: {_result['error']}. You can close this tab.</body></html>"
        else:
            body = "<html><body>Spotify authorized - you can close this tab and go back to Jarvis.</body></html>"
        self.wfile.write(body.encode("utf-8"))

    def log_message(self, format, *args):
        pass  # silence the default request-logging to stdout


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    client_id = get_password("spotify", "client_id")
    client_secret = get_password("spotify", "client_secret")
    if not client_id or not client_secret:
        print(
            "Missing spotify client_id/client_secret in keyring. Run these yourself first:\n"
            "  python -m keyring set spotify client_id\n"
            "  python -m keyring set spotify client_secret"
        )
        return 1

    state = secrets.token_urlsafe(16)
    auth_params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPES,
        "state": state,
    }
    auth_url = f"{AUTH_URL}?{urlencode(auth_params)}"

    server = HTTPServer(("127.0.0.1", 8888), _CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print("Opening browser for Spotify login/consent...")
    webbrowser.open(auth_url)

    server_thread.join(timeout=180)
    if "code" not in _result:
        print("Timed out waiting for the Spotify redirect. Try again.")
        return 1
    if _result.get("error"):
        print(f"Spotify returned an error: {_result['error']}")
        return 1
    if _result.get("state") != state:
        print("State mismatch - possible CSRF, aborting. Try again.")
        return 1

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": _result["code"],
            "redirect_uri": REDIRECT_URI,
        },
        auth=(client_id, client_secret),
        timeout=15,
    )
    if resp.status_code != 200:
        print(f"Token exchange failed ({resp.status_code}): {resp.text[:300]}")
        return 1

    tokens = resp.json()
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        print("No refresh_token in the response - unexpected. Response: " + str(tokens)[:300])
        return 1

    keyring.set_password("spotify", "refresh_token", refresh_token)
    print("Authorized. Refresh token stored in keyring (spotify/refresh_token). spotify_api.py is ready to use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
