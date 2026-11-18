# -*- coding: utf-8 -*-
"""On-demand model swapping for the local specialist-swarm experiment (op a_4e656055,
Nov 13 2026): his real hardware point - 10-15 models can't all stay loaded at once on 28.6GB
combined VRAM (checked live: only jarvis_coder_v3 at 4.4GB and LFM2.5-8B at 4.8GB fit clean on
the spare RTX 3060 without the main 35B's CPU/RAM MoE-offload tricks). The honest path to
MORE than 2-3 concurrent specialists on this hardware is swapping one model out for another on
the SAME GPU slot/port, not trying to hold them all live.

This module owns the swap slot: GPU1 (the 3060), port 8091 -- the card the main 35B (always on
CUDA0/port 8090) never touches, so swapping here can never disturb the production server.
"""
import os
import subprocess
import time
import urllib.error
import urllib.request

LLAMA_SERVER = r"C:\LocalAI\llama\llama-server.exe"
SWAP_PORT = 8091
SWAP_DEVICE = "CUDA1"
SWAP_HEALTH_URL = "http://127.0.0.1:%d/health" % SWAP_PORT
SWAP_CHAT_URL = "http://127.0.0.1:%d/v1/chat/completions" % SWAP_PORT

MODEL_CATALOG = {
    "lfm2.5-8b": r"C:\LocalAI\models\LFM2.5-8B-A1B\LFM2.5-8B-A1B-Q4_K_M.gguf",
    "jarvis_coder_v3": r"C:\LocalAI\models\jarvis_coder_v3.gguf",
    "phi-4-mini": r"C:\LocalAI\models\swarm_pool\Phi-4-mini-instruct-Q4_K_M.gguf",
    "deepseek-r1-qwen3-8b": r"C:\LocalAI\models\swarm_pool\DeepSeek-R1-0528-Qwen3-8B-Q4_K_M.gguf",
    "gemma-2-9b": r"C:\LocalAI\models\swarm_pool\gemma-2-9b-it-Q4_K_M.gguf",
    "llama-3.2-3b": r"C:\LocalAI\models\swarm_pool\Llama-3.2-3B-Instruct-Q4_K_M.gguf",
    "mistral-7b-v0.3": r"C:\LocalAI\models\swarm_pool\Mistral-7B-Instruct-v0.3-Q4_K_M.gguf",
}


def is_healthy(url=SWAP_HEALTH_URL, timeout=2):
    """Pure-ish (one real network call). True if the swap slot answers /health right now."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status == 200
    except (urllib.error.URLError, OSError):
        return False


def current_model_path(url="http://127.0.0.1:%d/v1/models" % SWAP_PORT, timeout=2):
    """Returns the gguf path the swap slot currently reports, or None if it's not up."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            import json
            data = json.loads(resp.read().decode("utf-8"))
            models = data.get("data") or []
            return models[0]["id"] if models else None
    except (urllib.error.URLError, OSError, ValueError, KeyError, IndexError):
        return None


def _pids_on_swap_port():
    """Real bug found live 2026-11-13: matching on Win32_Process.CommandLine for the process to
    kill was unreliable (a swap hung indefinitely, the old model never actually died). netstat
    -ano against the literal port is a more direct, reliable way to find who's actually bound to
    it. Returns a list of PIDs (possibly empty)."""
    result = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=10)
    pids = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[0] == "TCP" and parts[1].endswith(":%d" % SWAP_PORT) and parts[3] == "LISTENING":
            pids.add(parts[-1])
    return sorted(pids)


def stop_swap_slot(verify_timeout=15):
    """Kills whatever is actually bound to SWAP_PORT (found via netstat, not a CommandLine
    guess), by PID with taskkill /F, then BLOCKS until the port is genuinely free -- confirmed,
    not assumed. Returns True if something was actually stopped and confirmed dead."""
    pids = _pids_on_swap_port()
    if not pids:
        return False
    for pid in pids:
        subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, text=True, timeout=10)
    deadline = time.time() + verify_timeout
    while time.time() < deadline:
        if not _pids_on_swap_port() and not is_healthy():
            return True
        time.sleep(0.5)
    raise RuntimeError("stop_swap_slot: port %d still occupied after killing %s and waiting %ss" % (SWAP_PORT, pids, verify_timeout))


def launch_on_swap_slot(model_key, ctx=8192, timeout=90):
    """Launches MODEL_CATALOG[model_key] on the swap slot (GPU1, port 8091), waiting for it to
    answer /health. Raises RuntimeError if it never comes up within timeout. Does NOT stop
    whatever was already running there first -- call stop_swap_slot() before this for a real
    swap, this function alone just starts one."""
    path = MODEL_CATALOG[model_key]
    if not os.path.exists(path):
        raise RuntimeError("model file not found: %s" % path)
    args = (
        '-m "%s" --device %s --jinja -ngl 99 -c %d -fa on --host 127.0.0.1 --port %d'
        % (path, SWAP_DEVICE, ctx, SWAP_PORT)
    )
    ps = (
        "Start-Process -FilePath '%s' -ArgumentList '%s' -WorkingDirectory 'C:\\LocalAI\\llama' "
        "-WindowStyle Hidden -RedirectStandardOutput 'C:\\LocalAI\\swap_slot.log' "
        "-RedirectStandardError 'C:\\LocalAI\\swap_slot_err.log'"
    ) % (LLAMA_SERVER, args)
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=20)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_healthy():
            return True
        time.sleep(1)
    raise RuntimeError("model %s did not come healthy on port %d within %ds" % (model_key, SWAP_PORT, timeout))


def swap_to(model_key, ctx=8192):
    """The real round trip: stop whatever is on the swap slot (if anything), launch the
    requested model, confirm it's healthy. Returns True once the new model is genuinely live."""
    stop_swap_slot()
    return launch_on_swap_slot(model_key, ctx=ctx)


LLAMA_SWAP_CHAT_URL = "http://127.0.0.1:8095/v1/chat/completions"


def route_role_to(role, model_key, role_model_url, ctx=8192):
    """DEPRECATED path, kept for the hand-rolled single-slot swap (still useful standalone).
    Swaps the requested model onto the custom slot, then points role_model_url[role] (meant to
    be swarm_milestone1.ROLE_MODEL_URL, passed in rather than imported to avoid a circular
    import) at (SWAP_CHAT_URL, model_key). Returns True once the model is confirmed live."""
    swap_to(model_key, ctx=ctx)
    role_model_url[role] = (SWAP_CHAT_URL, model_key)
    return True


def route_role_to_llama_swap(role, model_key, role_model_url):
    """The real path as of 2026-11-13: llama-swap (config at C:\\LocalAI\\llama-swap\\config.yaml,
    listening on 8095) owns the actual process swap -- this just points role_model_url[role] at
    (LLAMA_SWAP_CHAT_URL, model_key); llama-swap swaps the real backend process on the first
    request that names it. No process management needed here at all."""
    role_model_url[role] = (LLAMA_SWAP_CHAT_URL, model_key)
    return True
