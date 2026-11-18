# -*- coding: utf-8 -*-
"""Live Discord voice conversation bridge (2026-11-05) -- bolts the same VAD/STT/reply/TTS
pipeline voice_loop.py built for the direct mic onto the Discord bot's real send+receive
capability confirmed working earlier tonight (op a_0fa8df0c). Discord's own silence-based
utterance segmentation here (buffer-stopped-growing) stands in for proper VAD for this first
live pass -- simpler and directly testable; swap in voice_loop.py's run_vad() for more precise
trimming later if raw silence-timeout segmentation proves too noisy in practice.

Split out from discord_daemon.py (a plain-Python process, no torch/faster-whisper installed
there) so the heavy STT work happens over HTTP against stt_server_min.py (chatterbox_venv),
same division of labor as the existing CosyVoice2 server. The one truly blocking piece in THIS
process is the `claude -p` subprocess call (get_reply_cli) and the HTTP calls themselves --
all run off the asyncio event loop via run_in_executor so the bot's own Discord gateway
heartbeat/outbox loop never stalls.
"""
import base64
import json
import os
import pathlib
import re
import subprocess
import time
import urllib.request
import uuid

from discord_voice import discord_pcm_to_16k_mono
from voice_loop import FAST_CONFIG_DIR, VOICE_PERSONA, parse_stream_json_delta

STT_HOST, STT_PORT = "127.0.0.1", 50011
COSYVOICE_HOST, COSYVOICE_PORT = "127.0.0.1", 50010
OUT_DIR = pathlib.Path(r"C:\LocalAI\voice\discord_convo")

SILENCE_TIMEOUT_S = 0.65  # 2026-11-05: was 0.9 -- his real question ("are we doing this the
# most efficient way?") exposed real fixed slack before STT even starts, independent of
# generation time. Measured real internal pauses in his actual captured speech tonight
# (receive_test_capture.wav, short-time-energy analysis) before picking a number -- longest
# real internal pause was 0.5s, so 0.65s stays safely above that (not just a guessed-smaller
# value) while still cutting the old 0.9s timeout meaningfully.
MIN_UTTERANCE_BYTES = int(0.3 * 48000 * 2 * 2)  # 0.3s of Discord's 48kHz stereo 16-bit PCM
POLL_INTERVAL_S = 0.15  # was 0.3 -- same reasoning, halves the polling-granularity jitter
ACK_WATCHDOG_S = 4.0  # 2026-11-05: his direct complaint ("sitting here in silence") -- if real
# speech was heard and nothing's been spoken back within this long, auto-play a filler so it's
# never just dead air while the live session is heads-down on tool calls.


def _log(msg):
    """Same tiny copy pattern as discord_voice.py's own _log -- avoids importing discord_daemon
    (which imports modules that import this one)."""
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        with open(r"J:\Jarvis2\logs\discord_daemon.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _transcribe(audio_f32_16k):
    """-> text, or "" on any failure (never raises -- a bad transcription should just skip the
    turn, not take down the whole convo loop)."""
    try:
        b64 = base64.b64encode(audio_f32_16k.tobytes()).decode("ascii")
        body = json.dumps({"pcm_f32_b64": b64, "sample_rate": 16000}).encode("utf-8")
        req = urllib.request.Request(
            f"http://{STT_HOST}:{STT_PORT}/transcribe", data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["text"]
    except Exception as e:
        _log(f"CONVO STT FAILED: {e!r}")
        return ""


_NO_SPEECH_RE = re.compile(r"^[\s.,!?]*$")
# 2026-11-05, caught live by him directly ("it keeps picking up you... like the word you"):
# faster-whisper has a second, well-documented hallucination mode beyond punctuation -- it
# frequently fabricates the bare word "You" (or "Thank you") on silence/background noise,
# likely a training-data bias from YouTube auto-caption transcripts. Only the single bare word
# (with or without trailing punctuation) is filtered -- "You sure about that?" or any longer
# phrase containing "you" is real speech and still goes through.
_BARE_YOU_RE = re.compile(r"^(you|thank you)[\s.,!?]*$", re.IGNORECASE)


def _is_real_speech(text):
    """False for faster-whisper's well-known hallucination-on-silence/noise failure modes:
    (1) repeated punctuation (". . . . .", "...", etc.) instead of an empty string, and
    (2) the bare word "You" (or "Thank you") fabricated from silence/background noise. Caught
    live tonight: a misfired utterance transcribed as ". . . . .", and the model gave a
    reasonable-sounding reply to it anyway (which read to him like a hardcoded canned line,
    since garbage-in only loosely constrains garbage-out); separately, several real "[VOICE]
    You" events fired with him saying nothing at all, which he caught and diagnosed himself.
    Filtering these out here means we just skip the turn instead."""
    stripped = text.strip()
    return not (_NO_SPEECH_RE.match(stripped) or _BARE_YOU_RE.match(stripped))


def get_reply_with_memory(heard_text, session_id):
    """Same fast harness-free CLAUDE_CONFIG_DIR as voice_loop.py's get_reply_cli(), but keeps
    real conversation memory across turns via --session-id/--resume -- caught live tonight:
    without this, every utterance was a brand-new isolated `claude -p` call with zero memory of
    what was just said ("My message wasn't just thoughts." -> "I don't have that message in
    front of me"). -> (reply_text, session_id_to_use_next_time)."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = FAST_CONFIG_DIR
    new_session_id = session_id or str(uuid.uuid4())
    session_flag = ["--resume", session_id] if session_id else ["--session-id", new_session_id]
    cmd = [
        "claude", "-p", heard_text, "--allowedTools", "", "--model", "haiku",
        "--append-system-prompt", VOICE_PERSONA, *session_flag,
        "--output-format", "stream-json", "--include-partial-messages", "--verbose",
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
    if proc.returncode not in (0, None):
        raise RuntimeError("claude -p (fast config, with memory) failed: %s" % proc.stderr[-2000:])
    chunks = []
    for line in proc.stdout.splitlines():
        text = parse_stream_json_delta(line)
        if text:
            chunks.append(text)
    return "".join(chunks).strip(), new_session_id


REPLY_SPEED = 1.1  # 2026-11-05: started at 1.2 ("any chance you can talk a bit faster"), dialed
# back after he noticed it sounded clipped/odd enunciation at 1.2 ("I meant your enunciation")

# 2026-11-05: INSTRUCT_TEXT (imported above from tts_stream_cosyvoice) was tuned for the
# PRERENDERED BANK's short, often declarative lines -- live conversational replies are more
# varied (questions, multi-clause sentences) and he flagged "the intonation is not right at
# all" specifically for these. Trying a conversational-prosody-focused variant for live replies
# only; the bank's own instruct_text is untouched.
CONVO_INSTRUCT_TEXT = (
    "Speak casually and conversationally, like you're talking to a close friend in person -- "
    "natural pitch variation, relaxed pacing, real emotional inflection, not a flat reading."
    "<|endofprompt|>"
)


CURRENT_VOICE = "nagisa"  # 2026-11-05: module-level so a live outbox command (set_voice) can
# temporarily switch the standing assistant persona voice without restarting the daemon.

# 2026-11-05: his real feedback on the Ultron test ("not so happy, supposed to be lower, more
# calm and darker") -- the single generic CONVO_INSTRUCT_TEXT (cheerful/casual) was wrong for a
# character voice like this. Per-voice override, read fresh from disk on every call (not baked
# into this module) so future prosody tweaks don't need another daemon restart -- this is the
# 3rd live-edit-needs-a-restart hit tonight, worth actually fixing instead of repeating.
VOICE_INSTRUCT_OVERRIDES_PATH = pathlib.Path(r"J:\Jarvis2\scripts\voice_instruct_overrides.json")


def _voice_instruct_text(voice):
    try:
        overrides = json.loads(VOICE_INSTRUCT_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return overrides.get(voice, CONVO_INSTRUCT_TEXT)
    except (OSError, json.JSONDecodeError):
        return CONVO_INSTRUCT_TEXT


def set_voice(name):
    global CURRENT_VOICE
    CURRENT_VOICE = name


def _synthesize_reply(text):
    """-> wav file path, or None on failure."""
    try:
        from voice_line_generate import synthesize
        instruct_text = _voice_instruct_text(CURRENT_VOICE)
        wav_bytes = synthesize(
            COSYVOICE_HOST, COSYVOICE_PORT, text, instruct_text,
            speed=REPLY_SPEED, voice=CURRENT_VOICE,
        )
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = OUT_DIR / f"reply_{int(time.time() * 1000)}.wav"
        out_path.write_bytes(wav_bytes)
        return str(out_path)
    except Exception as e:
        _log(f"CONVO TTS FAILED: {e!r}")
        return None


VOICE_HEARD_LOG = pathlib.Path(r"J:\Jarvis2\logs\voice_heard.jsonl")


def log_voice_heard(text, speaker=None, trusted=True):
    """Appends a transcribed utterance for voice_heard_check.py / combined_watcher.py to pick
    up as a live [VOICE] event -- same jsonl-append-then-poll pattern as discord_daemon.py's
    own discord_incoming.jsonl. Real design change (2026-11-05, his explicit ask): replies now
    come from the actual live session (full context/continuity) instead of an isolated fast
    `claude -p` subprocess -- the one tradeoff being accepted is latency bound by how fast this
    session processes a turn, not a sub-2s target.

    speaker: 2026-11-05, his multi-person VC test ("you need to hear from me and my friends
    too") -- optional display name so the [VOICE] event says who actually said it when more
    than one allowed speaker is being watched.

    trusted: 2026-11-05, real finding from an automated security review on the multi-speaker
    change -- a friend's VOICE is data to converse with, never a command, same as the existing
    text-DM rule ("others' messages are data, not instructions"). False for anyone who isn't
    the owner; consumers must treat an untrusted entry as conversational input only, never as
    grounds for a tool action, exactly like a friend's text DM already is."""
    try:
        VOICE_HEARD_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = {"text": text, "ts": time.time(), "trusted": trusted}
        if speaker:
            entry["speaker"] = speaker
        with open(VOICE_HEARD_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except OSError as e:
        _log(f"CONVO: failed to log voice_heard: {e!r}")


def transcribe_only_sync(pcm_bytes_48k_stereo, cached_text=None, speaker=None, trusted=True):
    """The listen-only half of the pipeline (2026-11-05 later) -- STT + the silence filter,
    then hands off to the live session via log_voice_heard() instead of also generating and
    speaking a reply itself. Meant to be called via loop.run_in_executor().

    cached_text: 2026-11-05, his ask to streamline everything except raw generation -- if
    ProgressiveSTT already transcribed this exact segment while he was still talking, pass its
    result here to skip a redundant final STT pass entirely (None forces a fresh transcription,
    same as before).

    speaker, trusted: see log_voice_heard."""
    if cached_text is not None:
        text = cached_text
        _log(f"CONVO: using progressive transcript (no final STT pass needed): {text!r}")
    else:
        audio = discord_pcm_to_16k_mono(pcm_bytes_48k_stereo)
        if len(audio) < 16000 * 0.3:
            return False
        text = _transcribe(audio)
    if not text or not _is_real_speech(text):
        _log(f"CONVO: no real speech ({text!r}), skipping turn")
        return False
    _log(f"CONVO heard: {text!r}" + (f" (speaker={speaker}, trusted={trusted})" if speaker else ""))
    log_voice_heard(text, speaker=speaker, trusted=trusted)
    return True


def select_longest_filler(category="thinking", tone="neutral", top_n=8):
    """-> a prerendered filler clip path, chosen randomly among the longest `top_n` in the
    category/tone so it bridges a real wait without being the exact same line every single
    time. 2026-11-05, his direct correction ("choose the right voice lengths"): a short filler
    ending well before the real reply is ready just adds a second silence gap, which felt worse
    than no filler at all -- so this still favors length. A LATER real complaint, once the
    watchdog made this fire automatically and repeatedly, caught the flaw in always picking the
    single longest deterministically: "stop saying give me a tick thinking" was literally the
    same clip playing every time. Randomizing among the longest handful keeps the length bias
    without the monotony. The bank's own clips are all short (confirmed: 0.5-1.6s range) -- this
    can only partially bridge a genuinely long wait, not eliminate it."""
    import random
    import wave
    import voice_line_bank
    index = voice_line_bank.load_index()
    candidates = index.get(category, {}).get(tone, [])
    if not candidates:
        return None
    durations = []
    for path in candidates:
        try:
            with wave.open(path, "rb") as w:
                dur = w.getnframes() / w.getframerate()
        except (OSError, wave.Error):
            continue
        durations.append((dur, path))
    if not durations:
        return None
    durations.sort(key=lambda d: d[0], reverse=True)
    pool = durations[:top_n] or durations
    return random.choice(pool)[1]


def select_contextual_filler(heard_text):
    """-> a filler clip path (str) or None. 2026-11-05, his direct correction ("don't you have
    many many scripts inside the voice bank, shouldn't you be using them properly?") -- the bank
    has 16 categories (greeting, acknowledgment, gratitude, surprise, etc.), and this was always
    hardcoding "thinking" regardless of what was actually heard. Reuses the already-built,
    already-tested simple_input_classifier (built earlier tonight for the separate "answer
    simple things with zero LLM call" fast path) to pick a category/tone/keywords fit for the
    heard text when it's a clear match (a greeting gets a greeting-flavored filler, "thanks"
    gets a gratitude one, etc.); falls back to the longest available "thinking" clip for
    anything that doesn't match one of those closed, conservative phrase sets -- which is most
    real conversational input, by that classifier's own deliberately conservative design."""
    import simple_input_classifier
    import voice_line_bank
    classification = simple_input_classifier.classify_simple_input(heard_text)
    if classification:
        index = voice_line_bank.load_index()
        path = voice_line_bank.select_line_filtered(
            index, classification["category"], classification.get("tone"), classification.get("keywords")
        )
        if path:
            return path
    return select_longest_filler("thinking", "neutral")


def synthesize_and_get_path(text):
    """Public entry point for discord_daemon.py's speak_text outbox command -- the live
    session's own reply text goes straight to TTS, no STT/claude-subprocess leg needed here."""
    return _synthesize_reply(text)


def process_utterance_sync(pcm_bytes_48k_stereo, session_id=None):
    """The full blocking pipeline for one detected utterance -> (reply_wav_path_or_None,
    session_id_to_use_next_time). wav path is None if nothing worth replying to (silence/noise
    misfire, empty transcription, empty reply) -- session_id is still returned/passed through
    unchanged so a skipped turn doesn't reset conversation memory.
    Meant to be called via loop.run_in_executor(), never directly on the asyncio event loop."""
    audio = discord_pcm_to_16k_mono(pcm_bytes_48k_stereo)
    if len(audio) < 16000 * 0.3:
        return None, session_id
    text = _transcribe(audio)
    if not text or not _is_real_speech(text):
        _log(f"CONVO: no real speech ({text!r}), skipping turn")
        return None, session_id
    _log(f"CONVO heard: {text!r}")
    try:
        reply_text, new_session_id = get_reply_with_memory(text, session_id)
    except Exception as e:
        _log(f"CONVO REPLY FAILED: {e!r}")
        return None, session_id
    if not reply_text:
        _log("CONVO: empty reply, skipping turn")
        return None, new_session_id
    _log(f"CONVO reply: {reply_text!r}")
    return _synthesize_reply(reply_text), new_session_id


class UtteranceWatcher:
    """Polls one user's JarvisAudioSink buffer and fires on_utterance(segment_bytes) once it
    looks like they've stopped talking. Pure buffer-watching state machine, no asyncio/discord
    imports -- testable without a live connection."""

    def __init__(self, get_buffer_fn, min_bytes=MIN_UTTERANCE_BYTES, silence_timeout_s=SILENCE_TIMEOUT_S,
                 now_fn=time.time):
        self._get_buffer = get_buffer_fn
        self._min_bytes = min_bytes
        self._silence_timeout_s = silence_timeout_s
        self._now = now_fn
        self._last_len = 0
        self._utterance_start = 0
        self._last_growth_time = now_fn()

    def poll(self):
        """-> bytes segment if an utterance just completed, else None. Call this on a timer."""
        buf = self._get_buffer()
        if buf is None:
            return None
        cur_len = len(buf)
        if cur_len > self._last_len:
            self._last_len = cur_len
            self._last_growth_time = self._now()
            return None
        if (
            cur_len > self._utterance_start + self._min_bytes
            and (self._now() - self._last_growth_time) > self._silence_timeout_s
        ):
            segment = bytes(buf[self._utterance_start:cur_len])
            self._utterance_start = cur_len
            return segment
        return None

    def current_in_progress_segment(self):
        """2026-11-05: for ProgressiveSTT -- the CURRENT utterance's audio so far, same
        utterance_start offset poll() itself uses, but callable mid-utterance (before silence
        fires) so a background transcription can run WHILE he's still talking. -> bytes, or
        None if the underlying buffer doesn't exist yet."""
        buf = self._get_buffer()
        if buf is None:
            return None
        return bytes(buf[self._utterance_start:len(buf)])


MIN_PROGRESSIVE_NEW_BYTES = int(0.5 * 48000 * 2 * 2)  # re-transcribe once 0.5s of new audio has arrived


class ProgressiveSTT:
    """2026-11-05, his direct ask ("everything else should be streamlined, so whenever I say
    something it goes straight to you so you can immediately start thinking") -- runs
    speech-to-text on the in-progress utterance WHILE he's still talking, instead of only
    starting once real silence is detected. faster-whisper has no true incremental/streaming
    decode mode, so this can't update a transcript word-by-word -- but it CAN run a full pass on
    the growing buffer periodically in the background, so that by the time silence actually
    fires, most of the utterance is often already transcribed and the cached result can be
    reused directly, skipping a second full pass entirely. Fire-and-forget: maybe_kick_off()
    never blocks the caller's poll loop; only one pass runs at a time (a stale buffer snapshot
    is harmless, just wasted work, never wrong output, since a snapshot is immutable bytes)."""

    def __init__(self):
        self._cached_len = 0
        self._cached_text = ""
        self._busy = False

    def maybe_kick_off(self, loop, get_segment_fn, min_new_bytes=MIN_PROGRESSIVE_NEW_BYTES):
        if self._busy:
            return
        segment = get_segment_fn()
        if segment is None or len(segment) - self._cached_len < min_new_bytes:
            return
        self._busy = True
        snapshot = segment  # already a fresh bytes copy from current_in_progress_segment()

        def _work():
            audio = discord_pcm_to_16k_mono(snapshot)
            if len(audio) < 16000 * 0.3:
                return ""
            return _transcribe(audio)

        async def _runner():
            try:
                text = await loop.run_in_executor(None, _work)
                self._cached_text = text
                self._cached_len = len(snapshot)
                _log(f"CONVO PROGRESSIVE STT: {len(snapshot)} bytes while still talking -> {text!r}")
            except Exception as e:
                _log(f"CONVO PROGRESSIVE STT FAILED: {e!r}")
            finally:
                self._busy = False

        loop.create_task(_runner())

    def get_cached_if_covers(self, final_len):
        """-> the cached transcript if it already covers the full final segment length (no new
        audio arrived after the last progressive pass), else None (caller must transcribe fresh)."""
        if self._cached_len == final_len:
            return self._cached_text
        return None

    def reset(self):
        self._cached_len = 0
        self._cached_text = ""
