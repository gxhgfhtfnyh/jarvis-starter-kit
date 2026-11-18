# -*- coding: utf-8 -*-
"""Minimal persistent STT server for the Discord live-conversation bridge (2026-11-05), same
pattern as cosyvoice_server_min.py: load the model ONCE at startup, serve plain HTTP
(http.server, no FastAPI), so discord_daemon.py's plain-Python process (no torch/faster-whisper
installed there) can get a transcription over a local HTTP call instead of needing those heavy
ML deps in its own environment.

Runs on chatterbox_venv's own interpreter (has faster-whisper + its CUDA DLL fixups already
proven in voice_loop.py's run_stt()).

POST /transcribe with JSON body {"pcm_f32_b64": str, "sample_rate": int} -- base64-encoded
float32 mono PCM at the given sample rate (discord_voice.py's discord_pcm_to_16k_mono() already
produces exactly this shape) -> JSON {"text": str}.

Usage (chatterbox_venv's python):
    python stt_server_min.py --port 50011
"""
import argparse
import base64
import json
import os
import pathlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

model = None  # set in main(), after model load


def _load_model():
    # same CUDA DLL fixup voice_loop.py's run_stt() already needed and proved working
    for sub in ("nvidia/cublas/bin", "nvidia/cudnn/bin", "nvidia/cuda_nvrtc/bin"):
        d = pathlib.Path(r"C:\LocalAI\voice\chatterbox_venv\Lib\site-packages") / sub
        if d.is_dir():
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
    from faster_whisper import WhisperModel
    return WhisperModel("base.en", device="cuda", compute_type="float16")


def _transcribe(pcm_f32_b64, sample_rate):
    audio = np.frombuffer(base64.b64decode(pcm_f32_b64), dtype="<f4")
    if sample_rate != 16000 and len(audio) > 1:
        idx = np.linspace(0, len(audio) - 1, int(len(audio) * 16000 / sample_rate))
        audio = np.interp(idx, np.arange(len(audio)), audio).astype(np.float32)
    segments, _info = model.transcribe(audio, beam_size=5)
    return " ".join(s.text.strip() for s in segments).strip()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # keep stdout quiet, same as cosyvoice_server_min.py

    def do_POST(self):
        if self.path != "/transcribe":
            self.send_response(404)
            self.end_headers()
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            text = _transcribe(body["pcm_f32_b64"], body.get("sample_rate", 16000))
            payload = json.dumps({"text": text}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        except Exception as e:
            payload = json.dumps({"error": str(e)}).encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404)
            self.end_headers()


def main():
    global model
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=50011)
    args = ap.parse_args()
    print("loading faster-whisper base.en...", flush=True)
    model = _load_model()
    print("model loaded, serving on port %d" % args.port, flush=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
