# -*- coding: utf-8 -*-
"""Discord voice channel capability: real send (play_file) and real receive (JarvisAudioSink),
layered on top of discord_daemon.py's ALREADY-EXISTING join/leave mechanism (the `join_voice`/
`leave_voice` outbox fields, added 2026-08-21 for a silent presence-only join -- "can u join the
vc that we are in rn... dont record anything or say anything just join"). Built 2026-11-05 after
the owner asked to use the existing bot for real voice chat instead of a local PC mic ("cant we
use this bot? i would like to continue using discord"), following real research (op a_7069edf2):
discord-ext-listening is confirmed dead, discord-ext-voice-recv is the real path, and Discord's
DAVE end-to-end encryption (mandatory for all voice since March 2026) needed the separate `davey`
package -- not installed before tonight, installed and verified
(`discord.voice_state.has_dave is True`) as part of this same build.

Deliberately NOT a parallel connection-tracking system: discord_daemon.py already tracks the
live connection via `guild.voice_client`/`self.voice_clients`, and its existing join_voice_id
handler was upgraded (connect(cls=voice_recv.VoiceRecvClient) instead of plain connect()) so one
connection now supports silent presence, send, AND receive -- this module only adds the
send/receive actions themselves, taking an already-connected VoiceClient as an argument rather
than looking guilds/channels up itself.

Pure, unit-tested logic: discord_pcm_to_16k_mono() -- converts Discord's receive format (48kHz
stereo 16-bit PCM) to the float32-mono-16kHz format voice_loop.py's run_vad()/run_stt() expect.

Honest scope for this build: real send (play_file) and real receive-into-a-per-user-buffer
(JarvisAudioSink via attach_listener) are wired into discord_daemon.py's outbox loop via two new
fields (`play_voice`, `listen_voice`). The buffer -> VAD -> STT -> reply -> TTS -> play-back full
loop is the next real step once the owner can actually test being heard -- that genuinely needs a
live human in a voice channel, which this build can't fake.
"""
import asyncio
import pathlib
import time

import numpy as np

_LOG_PATH = pathlib.Path(r"J:\Jarvis2\logs\discord_daemon.log")


def _log(msg):
    """Same file/format as discord_daemon.py's own _log -- a separate tiny copy here (not
    imported back from discord_daemon.py) to avoid a circular import, since discord_daemon.py
    is the one that imports FROM this module."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass

try:
    import discord
    from discord.ext import voice_recv
except ImportError:  # keeps this module importable (and its pure logic testable) without discord.py installed
    discord = None
    voice_recv = None

try:
    import davey
except ImportError:
    davey = None


def discord_pcm_to_16k_mono(pcm_bytes, discord_sr=48000, discord_channels=2, target_sr=16000):
    """Discord's receive PCM (signed 16-bit little-endian, 48kHz stereo by default) -> float32
    mono at target_sr, the exact format voice_loop.py's run_vad()/run_stt() expect. Pure."""
    if not pcm_bytes:
        return np.zeros(0, dtype=np.float32)
    audio_i16 = np.frombuffer(pcm_bytes, dtype="<i2")
    if discord_channels > 1:
        usable_len = (len(audio_i16) // discord_channels) * discord_channels
        audio_i16 = audio_i16[:usable_len].reshape(-1, discord_channels)
        audio = audio_i16.mean(axis=1).astype(np.float32) / 32768.0
    else:
        audio = audio_i16.astype(np.float32) / 32768.0
    if discord_sr != target_sr and len(audio) > 1:
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * target_sr / discord_sr))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    return audio


class JarvisAudioSink(voice_recv.AudioSink if voice_recv else object):
    """Buffers each speaker's raw PCM by user id.

    2026-11-05, later same night: wants_opus()=True now, by design -- voice_recv's own
    PacketDecryptor (confirmed via direct source read of its reader.py) only ever undoes
    Discord's OUTER transport encryption layer; it never calls davey's DAVE/E2EE decrypt at
    all, so under the DAVE-mandatory era its internal Opus decode throws
    OpusError('corrupted stream') on every real packet from a DAVE-capable client (which is
    now literally every normal Discord client). Rather than patch voice_recv's internal
    pipeline (real precedent that naive attempts at this exact fix corrupt audio -- Pycord's
    own community rewrite hit ~7% packet corruption trying it, per research done tonight),
    this sink takes the raw post-transport-decrypt-but-still-DAVE-encrypted bytes itself
    (data.opus), does the one missing DAVE decrypt call ourselves via the voice client's own
    dave_session, and only THEN runs our own Opus decode -- entirely inside our own code, so
    a mistake here only affects us, never voice_recv's shared internals.
    """

    def __init__(self):
        super().__init__()
        self.buffers = {}
        self.none_user_writes = 0  # 2026-11-05: real receive test came back empty -- counts
        # write() calls where voice_recv couldn't resolve an SSRC to a user, the other likely
        # silent-discard path (same shape as play()'s missing after= callback bug: a write that
        # "happens" but is invisible because the result just looks identical to nothing arriving)
        self.dave_decrypt_failures = 0  # real decrypt() exceptions, distinct from opus decode failures
        self.opus_decode_failures = 0
        self._opus_decoders = {}  # per-user discord.opus.Decoder() -- decoder state (PLC etc.) is per-stream

    def wants_opus(self):
        return True

    def write(self, user, data):
        if user is None:
            self.none_user_writes += 1
            return
        vc = getattr(self, "_voice_client", None)
        dave_session = getattr(getattr(vc, "_connection", None), "dave_session", None)
        raw = data.opus
        if dave_session is not None and davey is not None:
            try:
                raw = dave_session.decrypt(user.id, davey.MediaType.audio, raw)
            except Exception as e:
                self.dave_decrypt_failures += 1
                _log(f"DAVE DECRYPT FAILED for user {user.id}: {e!r}")
                return
        decoder = self._opus_decoders.setdefault(user.id, discord.opus.Decoder())
        try:
            pcm = decoder.decode(raw)
        except Exception as e:
            self.opus_decode_failures += 1
            _log(f"OPUS DECODE FAILED for user {user.id}: {e!r}")
            return
        self.buffers.setdefault(user.id, bytearray()).extend(pcm)

    def cleanup(self):
        self.buffers.clear()
        self._opus_decoders.clear()


async def play_file(voice_client, path, wait_if_busy=True, max_wait_s=30.0):
    """Basic voice SEND on an already-connected VoiceClient -- plays a wav/mp3 file via ffmpeg
    (FFmpegPCMAudio shells out to the real ffmpeg binary already confirmed installed on this
    machine). -> (ok: bool, msg: str).

    2026-11-05, his explicit ask (building the filler-then-reply pattern into a "smart script"):
    "make sure it doesn't clip out your voice halfway through" -- a filler clip played first,
    with the real reply following moments later, must never get cut off mid-word the way the
    old unconditional voice_client.stop() would. wait_if_busy=True (default) polls
    is_playing() and waits for whatever's currently playing to finish naturally before starting
    the new clip -- exactly the "no overlap, no added gap beyond the filler's own remaining
    duration" guarantee voice_loop.py's run_loop_live_handoff() already established for the
    local-mic pipeline. Pass wait_if_busy=False for a genuine interrupt (e.g. stop_voice)."""
    if voice_client is None or not voice_client.is_connected():
        return False, "not in a voice channel"
    p = pathlib.Path(path)
    if not p.exists():
        return False, "file not found: %s" % path
    if voice_client.is_playing():
        if wait_if_busy:
            waited = 0.0
            while voice_client.is_playing() and waited < max_wait_s:
                await asyncio.sleep(0.2)
                waited += 0.2
            if voice_client.is_playing():
                return False, "still busy after waiting %.0fs, not clipping it" % max_wait_s
        else:
            voice_client.stop()
    def _after(error):
        # 2026-11-05: discord.py's play() runs playback in its own background thread and
        # silently swallows any error unless an after= callback is given -- in a pythonw.exe
        # daemon with no console, that error previously vanished completely (he reported "nop"
        # after a logged "success", real bug: logging the .play() CALL succeeding is not the
        # same as the audio actually finishing/sending). Log whatever actually happened.
        if error:
            _log(f"PLAY VOICE finished WITH ERROR: {error!r}")
        else:
            _log("PLAY VOICE finished cleanly (no error from the after callback)")
    try:
        voice_client.play(discord.FFmpegPCMAudio(str(p)), after=_after)
        return True, "playing %s" % p.name
    except Exception as e:
        return False, "play failed: %r" % e


def attach_listener(voice_client):
    """Attaches a fresh JarvisAudioSink to an already-connected VoiceRecvClient.
    -> (ok: bool, msg: str, sink_or_None)."""
    if voice_client is None or not voice_client.is_connected():
        return False, "not in a voice channel", None
    if not hasattr(voice_client, "listen"):
        return False, "connected with a plain VoiceClient, not VoiceRecvClient - can't receive", None
    sink = JarvisAudioSink()

    def _after(error):
        # same play()-style gotcha: .listen() can fail (decode error, etc.) in a background
        # thread, silently, without this callback.
        if error:
            _log(f"LISTEN VOICE sink finished WITH ERROR: {error!r}")
        else:
            _log("LISTEN VOICE sink finished cleanly (no error from the after callback)")

    voice_client.listen(sink, after=_after)
    return True, "listening", sink


def detach_listener(voice_client):
    """-> (ok: bool, msg: str). Stops receiving, leaves the connection itself intact."""
    if voice_client is None or not hasattr(voice_client, "stop_listening"):
        return False, "not listening"
    voice_client.stop_listening()
    return True, "stopped listening"
