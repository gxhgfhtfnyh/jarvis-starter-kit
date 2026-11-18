# -*- coding: utf-8 -*-
"""Persistent warm embedding server (2026-11-12, real root cause of the recall-latency complaint: msearch.py/memsearch.py
spawn a fresh subprocess and reload the fastembed model from disk on every single call. recall_log.jsonl's last 50 real
calls measured min 1380ms / median 1784ms / mean 1895ms -- almost entirely bare interpreter startup (~70ms) + `from
fastembed import TextEmbedding` (~540ms) + model instantiation, not the actual vector search (~700 lines, milliseconds).
This loads the model and index ONCE, keeps them warm in memory, and serves queries over a local HTTP socket
(127.0.0.1 only) so repeat queries pay just the search cost.

Must run inside the isolated venv (fastembed only lives there), same as memsearch.py:
    C:\\LocalAI\\embed_trial\\venv\\Scripts\\pythonw.exe embed_server.py
Endpoints: GET /search?q=<question> -> same 'note | score | line' text memsearch.py prints (one per line, up to 5).
           GET /health -> "ok" (used by callers to decide warm-path vs subprocess fallback).
A background thread re-checks the vault for changed notes every REFRESH_SECONDS and swaps the in-memory index
atomically (unchanged files are free per memsearch.update_index's own mtime cache, so this is cheap when nothing
changed) -- queries never block on a refresh, they just read whatever the current swapped-in state is.
Kill switch: logs/embed_server_off.txt (checked at startup only, this is a long-running process not a per-call hook).
Pure core (unit tested with fakes, no fastembed/socket needed): parse_query(path), format_results(results)."""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

HOST = "127.0.0.1"
PORT = 8777
REFRESH_SECONDS = 300
LOGS = os.environ.get("JARVIS_LOGS") or os.path.join(os.path.dirname(HERE), "logs")


def parse_query(path):
    """Pure. URL path ('/search?q=a+b') -> the decoded query string, or None if there isn't one / wrong endpoint."""
    u = urlparse(path)
    if u.path != "/search":
        return None
    qs = parse_qs(u.query)
    vals = qs.get("q")
    return vals[0] if vals else None


def format_results(results):
    """Pure. results: [(score, relpath, line), ...] -> the same 'note | score | line' text memsearch.py prints."""
    return "\n".join("%s | %.2f | %s" % (p, s, l[:230]) for s, p, l in results)


class _Store:
    """Holds the current (meta, vectors, lines, embed_fn, query_embed_fn) under a lock, swapped atomically on refresh."""

    def __init__(self):
        self.lock = threading.Lock()
        self.meta = {}
        self.vectors = []
        self.lines = []
        self.embed = None
        self.query_embed = None

    def snapshot(self):
        with self.lock:
            return self.vectors, self.lines, self.query_embed

    def swap(self, meta, vectors, lines):
        with self.lock:
            self.meta, self.vectors, self.lines = meta, vectors, lines


def _build_embedders(model):
    import numpy as np

    def embed(ls):
        vs = np.asarray(list(model.embed(ls)), dtype="float32")
        return list(vs / (np.linalg.norm(vs, axis=1, keepdims=True) + 1e-9))

    def query_embed(q):
        return list(model.query_embed([q]))[0]

    return embed, query_embed


def _refresh_loop(store, stop_event):
    import memsearch

    while not stop_event.is_set():
        try:
            meta, vectors, lines = memsearch._load()
            meta, vectors, lines, n = memsearch.update_index(memsearch.VAULT, store.embed, meta, vectors, lines)
            if n:
                memsearch._save(meta, vectors, lines)
            store.swap(meta, vectors, lines)
        except Exception:  # noqa: BLE001 -- a bad refresh must never kill the server or wedge queries on stale data
            pass
        stop_event.wait(REFRESH_SECONDS)


def make_handler(store, rerank_fn=None):
    import memsearch

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # noqa: A003 -- quiet; this runs unattended, stdout noise serves nobody
            pass

        def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's own naming convention
            if urlparse(self.path).path == "/health":
                self._reply(200, "ok")
                return
            q = parse_query(self.path)
            if q is None:
                self._reply(404, "")
                return
            vectors, lines, query_embed = store.snapshot()
            results = memsearch.search(q, query_embed, vectors, lines, rerank_fn=rerank_fn) if lines else []
            self._reply(200, format_results(results))

        def _reply(self, code, text):
            body = text.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


def main(argv=None):
    if os.path.exists(os.path.join(LOGS, "embed_server_off.txt")):
        return 0
    import memsearch

    store = _Store()
    model = memsearch._model()
    store.embed, store.query_embed = _build_embedders(model)
    meta, vectors, lines = memsearch._load()
    store.swap(meta, vectors, lines)
    rerank_fn = memsearch._rerank_fn()  # loaded once at startup, same warm-process pattern as the embedding model

    stop_event = threading.Event()
    t = threading.Thread(target=_refresh_loop, args=(store, stop_event), daemon=True)
    t.start()

    httpd = ThreadingHTTPServer((HOST, PORT), make_handler(store, rerank_fn))
    print("embed_server listening on http://%s:%d (%d lines indexed)" % (HOST, PORT, len(store.lines)))
    try:
        httpd.serve_forever()
    finally:
        stop_event.set()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
