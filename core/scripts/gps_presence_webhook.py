# -*- coding: utf-8 -*-
"""GPS-based presence via iPhone Shortcuts geofence automations, built 2026-11-11
per his direct proposal ("how about connecting u to my gps so the presence mode
is better"). Not raw continuous GPS (battery drain, overkill, more invasive) -
an iPhone Shortcuts "arrive at Home" / "leave Home" location-trigger automation
POSTs one small, instant event here the moment he actually crosses the geofence,
which is strictly more precise than the current wifi-MAC-based detection (that
only notices once wifi actually drops, lagging behind a real departure/arrival).

Reached over Tailscale (already installed and running on this PC, confirmed
live: `tailscale status` shows this box at 100.89.168.26) - his phone needs the
Tailscale app added to the same account, giving it a private tailnet IP so the
Shortcuts automation can reach this server without anything being exposed to
the public internet. Binds to the Tailscale interface specifically (not
0.0.0.0), a defense-in-depth narrowing even though Tailscale's own ACLs already
gate who can reach this box at all.

Auth: a random token (Windows Credential Manager, service 'jarvis-gps-webhook',
username 'token' - never written in plain text here or in the vault) must be
sent as the 'token' field; any request without a matching token is rejected
and logged.

Usage:
    python gps_presence_webhook.py            # runs the server (foreground)
Phone side (Shortcuts, per automation):
    When "I Arrive" at Home -> Get Contents of URL
        POST http://<tailscale-ip>:8743/presence   (plain HTTP - Tailscale's own
        WireGuard tunnel already encrypts everything between tailnet devices;
        this server has no TLS of its own and isn't meant to be reachable any
        other way)
        Body (JSON): {"event": "arrive", "token": "<the real token>"}
    When "I Leave" Home -> same, body {"event": "leave", "token": "..."}
"""
import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import keyring

PORT = 8743
LOG_PATH = r"J:\Jarvis2\logs\gps_presence_webhook.log"
SET_PRESENCE = [sys.executable, r"J:\Jarvis2\scripts\set_presence_mode.py"]
EVENT_TO_MODE = {"arrive": "home", "leave": "away"}


def _log(line):
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def _tailscale_ip():
    """The Tailscale interface's own IP, to bind narrowly instead of 0.0.0.0."""
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True,
                              timeout=10, creationflags=0x08000000).stdout.strip()
        return out.splitlines()[0] if out else None
    except Exception as e:
        _log(f"could not read tailscale ip: {e!r}")
        return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # our own _log() call below covers what matters; silence the default stderr noise

    def do_POST(self):
        if self.path != "/presence":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, OSError):
            self.send_response(400)
            self.end_headers()
            _log(f"bad request body from {self.client_address[0]}")
            return

        real_token = keyring.get_password("jarvis-gps-webhook", "token")
        if not real_token or body.get("token") != real_token:
            self.send_response(401)
            self.end_headers()
            _log(f"REJECTED (bad/missing token) from {self.client_address[0]}")
            return

        event = body.get("event")
        mode = EVENT_TO_MODE.get(event)
        if not mode:
            self.send_response(400)
            self.end_headers()
            _log(f"unknown event {event!r} from {self.client_address[0]}")
            return

        import os
        env = dict(os.environ, PRESENCE_CHANGED_BY="gps")
        try:
            subprocess.run(SET_PRESENCE + [mode], capture_output=True, timeout=30, env=env,
                            creationflags=0x08000000)
            _log(f"event={event} -> presence set to {mode} (from {self.client_address[0]})")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        except Exception as e:
            _log(f"ERROR applying {event}: {e!r}")
            self.send_response(500)
            self.end_headers()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    bind_ip = _tailscale_ip()
    if not bind_ip:
        print("ERROR: could not determine Tailscale IP - is Tailscale running? Refusing to bind "
              "to 0.0.0.0 as a fallback (would expose this beyond the tailnet).", flush=True)
        sys.exit(1)
    server = HTTPServer((bind_ip, PORT), Handler)
    print(f"gps_presence_webhook listening on {bind_ip}:{PORT} (Tailscale interface only)", flush=True)
    _log(f"server started on {bind_ip}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
