# -*- coding: utf-8 -*-
"""
Local Spotify integration via Windows' own Global System Media Transport
Controls (GSMTC) - the same system any media app plugs into so your
keyboard's play/pause/next media keys work regardless of which app is
playing. No Spotify account linking, no OAuth, works the moment Spotify is
open and playing - built 2026-08-12 at the owner's request, as the "local"
half of a two-part Spotify integration (the other half, full Web API search/
playlist control, needs the owner to register a developer app himself first).

Real limitation, stated plainly: this only sees/controls whatever's already
loaded and playing in Spotify on THIS PC. It cannot search for a new track,
queue something not already playing, or control Spotify running elsewhere
(phone, another PC) - that needs the Web API half.

Volume is separate from GSMTC (which only does transport controls) - uses
pycaw to control Spotify's own per-application volume specifically, not the
whole system's volume.

Usage:
    python spotify_control.py now-playing
    python spotify_control.py play | pause | play-pause | next | previous
    python spotify_control.py volume [0-100]     # no number = read current
"""
import asyncio
import sys

from winsdk.windows.media.control import (
    GlobalSystemMediaTransportControlsSessionManager as SessionManager,
)


async def _find_spotify_session():
    manager = await SessionManager.request_async()
    for s in manager.get_sessions():
        if "spotify" in (s.source_app_user_model_id or "").lower():
            return s
    return None


async def _get_now_playing_session():
    """Read-only path only: prefer an active Spotify session, fall back to
    whatever the system currently considers "current" if Spotify isn't
    found, so a plain status read is still useful with e.g. a browser tab
    playing instead. Reading is harmless regardless of which app it hits."""
    session = await _find_spotify_session()
    if session is not None:
        return session
    manager = await SessionManager.request_async()
    return manager.get_current_session()


async def now_playing_async():
    session = await _get_now_playing_session()
    if session is None:
        return None
    info = await session.try_get_media_properties_async()
    playback = session.get_playback_info()
    timeline = session.get_timeline_properties()
    status_names = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}
    return {
        "app": session.source_app_user_model_id,
        "title": info.title,
        "artist": info.artist,
        "album": info.album_title,
        "status": status_names.get(int(playback.playback_status), "unknown"),
        "position_sec": timeline.position.total_seconds() if timeline.position else None,
        "duration_sec": timeline.end_time.total_seconds() if timeline.end_time else None,
    }


async def _control_async(action):
    # Deliberately strict, unlike now_playing_async's read path: a real bug
    # found live 2026-08-12 - falling back to "whatever the system's current
    # session is" meant a test command sent to this function actually
    # toggled play/pause on a YouTube video in Chrome, not Spotify, because
    # Spotify wasn't open. A transport command must never land on the wrong
    # app just because Spotify wasn't running.
    session = await _find_spotify_session()
    if session is None:
        print("Spotify isn't running/playing - refusing to send a command to a different app.")
        return False
    ok = await {
        "play": session.try_play_async,
        "pause": session.try_pause_async,
        "play-pause": session.try_toggle_play_pause_async,
        "next": session.try_skip_next_async,
        "previous": session.try_skip_previous_async,
    }[action]()
    return bool(ok)


def _spotify_volume_endpoint():
    from pycaw.pycaw import AudioUtilities

    for session in AudioUtilities.GetAllSessions():
        proc = session.Process
        if proc and "spotify" in proc.name().lower():
            return session.SimpleAudioVolume
    return None


def get_volume():
    vol = _spotify_volume_endpoint()
    if vol is None:
        return None
    return round(vol.GetMasterVolume() * 100)


def set_volume(level):
    level = max(0, min(100, level))
    vol = _spotify_volume_endpoint()
    if vol is None:
        return False
    vol.SetMasterVolume(level / 100, None)
    return True


GSMTC_TIMEOUT_SEC = 3
# Real bug found live 2026-08-12: the WinRT GSMTC session-manager call has no
# built-in timeout, and can hang indefinitely if Windows' media session state
# gets stuck (reproduced with Spotify not even running - this is a WinRT/OS-level
# hang, not Spotify-specific). avatar.py polls now_playing() synchronously on its
# Tkinter main thread every 5s with no timeout of its own - the first stuck call
# froze the entire GUI permanently (confirmed: process alive, CPU delta 0, window
# reported Not Responding). Every WinRT entry point must be timeout-wrapped so a
# stuck OS-level session can never hang a caller indefinitely.


def now_playing():
    try:
        return asyncio.run(asyncio.wait_for(now_playing_async(), timeout=GSMTC_TIMEOUT_SEC))
    except asyncio.TimeoutError:
        return None


def control(action):
    try:
        return asyncio.run(asyncio.wait_for(_control_async(action), timeout=GSMTC_TIMEOUT_SEC))
    except asyncio.TimeoutError:
        print("Timed out talking to Windows media session manager.")
        return False


def _cli():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "now-playing":
        np = now_playing()
        if np is None:
            print("Nothing playing (no active media session).")
        else:
            dur = f"{int(np['position_sec'] or 0)}s / {int(np['duration_sec'] or 0)}s"
            print(f"{np['status'].upper()}: {np['title']} - {np['artist']} ({np['album']}) [{dur}] via {np['app']}")
        return

    if cmd in ("play", "pause", "play-pause", "next", "previous"):
        # _control_async already prints its own specific reason on failure
        # (e.g. "Spotify isn't running") - only add a message on success.
        if control(cmd):
            print("OK")
        return

    if cmd == "volume":
        if len(sys.argv) >= 3:
            level = int(sys.argv[2])
            ok = set_volume(level)
            print(f"Set volume to {level}%" if ok else "Spotify not found in active audio sessions.")
        else:
            v = get_volume()
            print(f"Current Spotify volume: {v}%" if v is not None else "Spotify not found in active audio sessions.")
        return

    print(f"Unknown command {cmd!r}.")
    print(__doc__)
    sys.exit(1)


if __name__ == "__main__":
    _cli()
