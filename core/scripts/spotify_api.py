# -*- coding: utf-8 -*-
"""
Full Spotify Web API client - the "anywhere" half of Spotify integration
(spotify_control.py is the local-only half, via Windows media controls).
Search, queue, playlists, and playback control that works even when
Spotify's running on the owner's phone instead of this PC. Built 2026-08-12.

Requires spotify_auth.py to have been run once already (stores a refresh
token in keyring). Access tokens are short-lived (1 hour) and refreshed
transparently on every call - no caching, since this isn't called often
enough for that to matter.

Usage:
    python spotify_api.py search "<query>" [track|album|artist|playlist]
    python spotify_api.py play-search "<query>"     # search + play the first track result
    python spotify_api.py now-playing
    python spotify_api.py play | pause | next | previous
    python spotify_api.py volume <0-100>
    python spotify_api.py playlists
    python spotify_api.py queue "<query>"
"""
import sys

import keyring
import requests

from jarvis_keyring import get_password

TOKEN_URL = "https://accounts.spotify.com/api/token"
API_BASE = "https://api.spotify.com/v1"


class SpotifyAuthError(Exception):
    pass


def _get_access_token():
    client_id = get_password("spotify", "client_id")
    client_secret = get_password("spotify", "client_secret")
    refresh_token = get_password("spotify", "refresh_token")
    if not (client_id and client_secret and refresh_token):
        raise SpotifyAuthError(
            "Missing spotify credentials in keyring. Run spotify_auth.py first "
            "(after client_id/client_secret are set)."
        )
    resp = requests.post(
        TOKEN_URL,
        data={"grant_type": "refresh_token", "refresh_token": refresh_token},
        auth=(client_id, client_secret),
        timeout=15,
    )
    if resp.status_code != 200:
        raise SpotifyAuthError(f"Token refresh failed ({resp.status_code}): {resp.text[:300]}")
    data = resp.json()
    # Spotify sometimes rotates the refresh token on use - persist the new one if given.
    if data.get("refresh_token"):
        keyring.set_password("spotify", "refresh_token", data["refresh_token"])
    return data["access_token"]


def _request(method, path, **kwargs):
    token = _get_access_token()
    headers = kwargs.pop("headers", {})
    headers["Authorization"] = f"Bearer {token}"
    resp = requests.request(method, f"{API_BASE}{path}", headers=headers, timeout=15, **kwargs)
    if resp.status_code == 204:
        return None
    if resp.status_code >= 400:
        raise RuntimeError(f"Spotify API {method} {path} failed ({resp.status_code}): {resp.text[:300]}")
    if resp.content:
        # Real bug found live 2026-08-12: a transport command with zero
        # registered devices returned HTTP 200 with a non-JSON opaque body
        # (looked like an edge-infra token, not a normal API response) -
        # .json() crashed unconditionally on any non-empty success body.
        # Don't assume every 2xx body is JSON; fall back to raw text.
        try:
            return resp.json()
        except ValueError:
            return resp.text
    return None


def get_devices():
    data = _request("GET", "/me/player/devices")
    return data.get("devices", []) if data else []


def _require_device():
    """Playback-control commands give a much clearer signal checked against
    the real devices list first, rather than sending the command anyway and
    getting back whatever ambiguous response Spotify has for "no device"."""
    devices = get_devices()
    if not devices:
        raise RuntimeError("No Spotify devices found - open Spotify (phone, PC, anywhere) first.")
    return devices


def search(query, item_type="track", limit=10):
    data = _request("GET", "/search", params={"q": query, "type": item_type, "limit": limit})
    return data.get(f"{item_type}s", {}).get("items", [])


def get_current_playback():
    return _request("GET", "/me/player")


def play(track_uris=None, device_id=None):
    _require_device()
    params = {"device_id": device_id} if device_id else {}
    body = {"uris": track_uris} if track_uris else {}
    return _request("PUT", "/me/player/play", params=params, json=body)


def pause():
    _require_device()
    return _request("PUT", "/me/player/pause")


def next_track():
    _require_device()
    return _request("POST", "/me/player/next")


def previous_track():
    _require_device()
    return _request("POST", "/me/player/previous")


def set_volume(percent):
    _require_device()
    percent = max(0, min(100, percent))
    return _request("PUT", "/me/player/volume", params={"volume_percent": percent})


def queue_track(track_uri):
    return _request("POST", "/me/player/queue", params={"uri": track_uri})


def get_playlists(limit=50):
    data = _request("GET", "/me/playlists", params={"limit": limit})
    return data.get("items", [])


def create_playlist(name, description="", public=False):
    me = _request("GET", "/me")
    return _request(
        "POST", f"/users/{me['id']}/playlists",
        json={"name": name, "description": description, "public": public},
    )


def add_to_playlist(playlist_id, track_uris):
    return _request("POST", f"/playlists/{playlist_id}/tracks", json={"uris": track_uris})


def get_top_tracks(limit=20, time_range="medium_term"):
    """time_range: short_term (~4wk), medium_term (~6mo), long_term (years).
    Needs the user-top-read scope (added 2026-08-16, the owner asked directly)."""
    data = _request("GET", "/me/top/tracks", params={"limit": limit, "time_range": time_range})
    return data.get("items", [])


def get_top_artists(limit=20, time_range="medium_term"):
    data = _request("GET", "/me/top/artists", params={"limit": limit, "time_range": time_range})
    return data.get("items", [])


def get_recently_played(limit=20):
    """Needs the user-read-recently-played scope (added 2026-08-16)."""
    data = _request("GET", "/me/player/recently-played", params={"limit": limit})
    return data.get("items", [])


def _fmt_track(t):
    artists = ", ".join(a["name"] for a in t.get("artists", []))
    return f"{t['name']} - {artists} [{t['uri']}]"


def _cli():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]

    try:
        if cmd == "search":
            query = sys.argv[2]
            item_type = sys.argv[3] if len(sys.argv) > 3 else "track"
            results = search(query, item_type)
            if not results:
                print("No results.")
            for item in results:
                if item_type == "track":
                    print(_fmt_track(item))
                else:
                    print(f"{item.get('name')} [{item.get('uri')}]")
            return

        if cmd == "play-search":
            query = sys.argv[2]
            results = search(query, "track", limit=1)
            if not results:
                print("No results.")
                return
            track = results[0]
            play(track_uris=[track["uri"]])
            print(f"Playing: {_fmt_track(track)}")
            return

        if cmd == "now-playing":
            data = get_current_playback()
            if not data or not data.get("item"):
                print("Nothing playing (or no active device).")
                return
            item = data["item"]
            state = "PLAYING" if data.get("is_playing") else "PAUSED"
            print(f"{state}: {_fmt_track(item)} on {data.get('device', {}).get('name', '?')}")
            return

        if cmd == "play":
            play()
            print("OK")
            return
        if cmd == "pause":
            pause()
            print("OK")
            return
        if cmd == "next":
            next_track()
            print("OK")
            return
        if cmd == "previous":
            previous_track()
            print("OK")
            return

        if cmd == "volume":
            set_volume(int(sys.argv[2]))
            print("OK")
            return

        if cmd == "playlists":
            for pl in get_playlists():
                # Real API shape found live 2026-08-12: this endpoint's
                # track-count field is "items", not the "tracks" field
                # older docs/memory would suggest - confirmed against the
                # actual response, not assumed.
                count = pl.get("items", {}).get("total", "?")
                print(f"{pl['name']} ({count} tracks) [{pl['uri']}]")
            return

        if cmd == "queue":
            query = sys.argv[2]
            results = search(query, "track", limit=1)
            if not results:
                print("No results.")
                return
            track = results[0]
            queue_track(track["uri"])
            print(f"Queued: {_fmt_track(track)}")
            return

        print(f"Unknown command {cmd!r}.")
        print(__doc__)
        sys.exit(1)

    except SpotifyAuthError as e:
        print(str(e))
        sys.exit(1)
    except RuntimeError as e:
        msg = str(e)
        if "404" in msg and "player" in msg.lower():
            print("No active Spotify device found - open Spotify on your phone/PC first.")
        else:
            print(msg)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
