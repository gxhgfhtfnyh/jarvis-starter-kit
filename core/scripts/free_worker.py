"""Hand a small NON-PRIVATE job to the free online model (Cloudflare GLM) through the local OmniRoute gateway.
Usage: python free_worker.py "prompt" [--file path] [--max-tokens N]
Only for public or harmless text (summaries, simple code, formatting). Anything sent here leaves this PC and goes to Cloudflare's servers.
The gateway listens on 127.0.0.1:20128 only; the key used is restricted to the 'claude-free' combo (stored next to the install, not in this file).
Always review the answer yourself before using it: the free model is weaker than Claude."""
import argparse, json, pathlib, sys, time, urllib.request

KEY_FILE = pathlib.Path(r"C:\LocalAI\omniroute_trial\claude_key.txt")
URL = "http://127.0.0.1:20128/v1/chat/completions"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt")
    ap.add_argument("--file", help="text file to include after the prompt")
    ap.add_argument("--max-tokens", type=int, default=1500)
    a = ap.parse_args()
    text = a.prompt
    if a.file:
        text += "\n\n---\n" + pathlib.Path(a.file).read_text(encoding="utf-8", errors="replace")
    req = urllib.request.Request(URL, data=json.dumps({
        "model": "claude-free", "max_tokens": a.max_tokens,
        "messages": [{"role": "user", "content": text}]}).encode(),
        headers={"Authorization": "Bearer " + KEY_FILE.read_text().strip(), "Content-Type": "application/json"})
    # 2026-10-29: this text leaves the PC (Cloudflare), so refuse anything that looks like private data (emails, ids, secrets), and log
    # which model answered, how fast, and how many tokens, so the chain order can be judged by evidence (route_log.jsonl).
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from route_job import scan_private_data
    hits = scan_private_data(text)
    if hits:
        sys.exit("free_worker refused: the text looks like it contains private data (%s). Do this job yourself." % ", ".join(hits[:4]))
    t = time.time()
    try:
        d = json.load(urllib.request.urlopen(req, timeout=180))
    except Exception as e:
        sys.exit("free_worker failed (is OmniRoute running on 127.0.0.1:20128?): %s" % e)
    secs = time.time() - t
    print(d["choices"][0]["message"]["content"])
    u = d.get("usage") or {}
    print("\n[free model %s, %.1fs]" % (d.get("model", "?"), secs), file=sys.stderr)
    try:
        with open(r"C:\LocalAI\worker_jobs\route_log.jsonl", "a", encoding="utf-8") as lf:
            lf.write(json.dumps({"t": time.strftime("%F %T"), "event": "single_shot", "model": d.get("model"), "secs": round(secs, 1),
                                 "input_tokens": u.get("prompt_tokens"), "output_tokens": u.get("completion_tokens")}) + chr(10))
    except OSError:
        pass

if __name__ == "__main__":
    main()
