# -*- coding: utf-8 -*-
"""Meaning-based vault search (2026-10-29, the owner: better access and searching of the memory vault).
Measured on memory_eval.py: brain.py 10/20 main + 5/8 held-out; lexical paragraph finder 13/20 + 6/8; semantic rerank 13/20 + 9/10 held-out (but 30 s per question
because it embedded thousands of lines per query). This version embeds every vault line once (BAAI/bge-small-en-v1.5 through fastembed, local, no network after the
model download), caches the vectors by file mtime, and ranks lines by semantic similarity plus a rare-word bonus, so a question costs about a second.
    C:\\LocalAI\\embed_trial\\venv\\Scripts\\python.exe memsearch.py "question words"        (fastembed lives only in that isolated venv)
    ... memsearch.py --update                                                              (refresh the index and exit)
Output: up to 5 lines 'note path | score | the line'. Index: J:\\Jarvis2\\logs\\memsearch_index.npz + memsearch_meta.json (rebuildable at any time).
2026-11-17 (the owner: "fix it" re: the RAG research finding that reranking is the single biggest accuracy lever in a retrieve pipeline): the hybrid score above
picks a wider pool of RERANK_POOL candidates, then a small local cross-encoder (Xenova/ms-marco-MiniLM-L-6-v2, 80MB, via fastembed's TextCrossEncoder -- already
installed, no new dependency) rescores just that pool and the final top-k comes from the cross-encoder, not the hybrid score. The hybrid score still decides which
lines are even worth the reranker's attention; reranking never runs over the whole vault. Falls back to the un-reranked hybrid order on any reranker failure
(model missing, OOM, etc) so a broken reranker degrades search quality, never breaks it.
Pure core (unit tested with a fake embedder/reranker): file_lines, tokens, lexical, combine, update_index, search, rerank_candidates."""
import json
import math
import os
import re
import sys

VAULT = r"J:\Jarvis-Brain"
INDEX = r"J:\Jarvis2\logs\memsearch_index.npz"
META = r"J:\Jarvis2\logs\memsearch_meta.json"
MODEL_NAME = "BAAI/bge-small-en-v1.5"
RERANK_MODEL_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"
RERANK_POOL = 20  # candidates handed to the cross-encoder; must be >= k for reranking to do anything
CACHE_DIR = r"C:\LocalAI\embed_trial\cache"
STOP = frozenset("the a an and or of to in on at for is are was were do does did what which when who how many much that this with from it its be by as pick give".split())
SKIP_DIRS = (".obsidian", ".git")


def file_lines(text):
    """Searchable lines of a note: stripped, 20 to 599 characters."""
    return [l.strip() for l in text.splitlines() if 20 < len(l.strip()) < 600]


def tokens(text):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9][a-z0-9.:'-]*", text.lower())}


def query_words(q):
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9][a-z0-9.:'-]*", q.lower()) if w not in STOP}


def lexical(qwords, line_tokens):
    """Rare-word score per line: idf over all lines for each query word that appears as a whole word."""
    n = max(len(line_tokens), 1)
    df = {w: sum(1 for t in line_tokens if w in t) for w in qwords}
    return [sum(math.log(1 + n / (1 + df[w])) for w in qwords if w in t) for t in line_tokens]


def combine(sem, lex, alpha=0.7):
    """alpha * semantic + (1-alpha) * lexical, each scaled to 0..1 by its own maximum (a zero maximum counts as all zeros)."""
    smax = max(sem) if len(sem) else 0
    lmax = max(lex) if len(lex) else 0
    return [alpha * (s / smax if smax > 0 else 0.0) + (1 - alpha) * (l / lmax if lmax > 0 else 0.0) for s, l in zip(sem, lex)]


def update_index(vault, embed, meta, vectors, lines):
    """Bring (meta, vectors, lines) up to date with the vault. meta: {relpath: [mtime, first_row, count]}; lines: [(relpath, text)]; vectors: list of vectors.
    Unchanged files keep their rows, changed or new files are re-embedded, deleted files are dropped. Returns (meta, vectors, lines, files_embedded)."""
    new_meta, new_vecs, new_lines, embedded = {}, [], [], 0
    for d, _dirs, files in os.walk(vault):
        if any(s in d for s in SKIP_DIRS):
            continue
        for f in sorted(files):
            if not f.endswith(".md"):
                continue
            full = os.path.join(d, f)
            rel = os.path.relpath(full, vault)
            mtime = os.path.getmtime(full)
            old = meta.get(rel)
            first = len(new_lines)
            if old and abs(old[0] - mtime) < 1e-6:
                rows = range(old[1], old[1] + old[2])
                new_vecs.extend(vectors[i] for i in rows)
                new_lines.extend(lines[i] for i in rows)
            else:
                try:
                    ls = file_lines(open(full, encoding="utf-8", errors="ignore").read())
                except OSError:
                    continue
                if ls:
                    new_vecs.extend(embed(ls))
                    new_lines.extend((rel, l) for l in ls)
                    embedded += 1
            new_meta[rel] = [mtime, first, len(new_lines) - first]
    return new_meta, new_vecs, new_lines, embedded


def rerank_candidates(q, score_fn, pool):
    """[(rerank_score, relpath, line)] for `pool` ([(relpath, line), ...]), ordered best first.
    score_fn(q, [text, ...]) -> [float, ...] is the cross-encoder call, injected so tests need no real model."""
    if not pool:
        return []
    scores = list(score_fn(q, [l for _, l in pool]))
    order = sorted(range(len(pool)), key=lambda i: -scores[i])
    return [(scores[i], pool[i][0], pool[i][1]) for i in order]


def search(q, embed_query, vectors, lines, k=5, rerank_fn=None, pool_k=RERANK_POOL):
    """[(score, relpath, line)] best k lines for the question; vectors must be unit length rows of a matrix.
    rerank_fn, if given, is a (q, texts)->scores callable: the hybrid score picks a pool_k-wide candidate pool,
    rerank_fn rescores just that pool, and the returned top-k is by rerank score. Any rerank_fn failure falls
    back to the plain hybrid-score top-k silently -- a broken reranker must never break search."""
    import numpy as np
    if not len(lines):
        return []
    m = np.asarray(vectors, dtype="float32")
    qv = np.asarray(embed_query(q), dtype="float32")
    qv = qv / (np.linalg.norm(qv) + 1e-9)
    sem = (m @ qv).tolist()
    lex = lexical(query_words(q), [tokens(l) for _, l in lines])
    score = combine(sem, lex)
    n = max(k, pool_k) if rerank_fn else k
    order = sorted(range(len(lines)), key=lambda i: -score[i])[:n]
    if rerank_fn is None:
        return [(score[i], lines[i][0], lines[i][1]) for i in order]
    try:
        return rerank_candidates(q, rerank_fn, [lines[i] for i in order])[:k]
    except Exception:
        return [(score[i], lines[i][0], lines[i][1]) for i in order[:k]]


def _model():
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL_NAME, cache_dir=CACHE_DIR)


def _rerank_fn():
    """-> a (q, texts)->scores callable backed by the real local cross-encoder, or None if it can't load
    (model not cached yet and offline, OOM, etc) -- search() already falls back cleanly when this is None."""
    try:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        ce = TextCrossEncoder(RERANK_MODEL_NAME, cache_dir=CACHE_DIR)
        return lambda q, texts: list(ce.rerank(q, texts))
    except Exception:
        return None


def _load():
    import numpy as np
    try:
        meta = json.load(open(META, encoding="utf-8"))
        z = np.load(INDEX, allow_pickle=False)
        return meta["files"], list(z["vectors"]), [tuple(x) for x in meta["lines"]]
    except (OSError, ValueError, KeyError):
        return {}, [], []


def _save(meta, vectors, lines):
    import numpy as np
    np.savez_compressed(INDEX, vectors=np.asarray(vectors, dtype="float32"))
    json.dump({"files": meta, "lines": [list(x) for x in lines]}, open(META, "w", encoding="utf-8"))


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    import numpy as np
    model = _model()

    def embed(ls):
        vs = np.asarray(list(model.embed(ls)), dtype="float32")
        return list(vs / (np.linalg.norm(vs, axis=1, keepdims=True) + 1e-9))
    meta, vectors, lines = _load()
    no_update = "--no-update" in argv              # a hook must never pay for a re-embed (a changed vault took 37 s once): search the index as it is
    no_rerank = "--no-rerank" in argv               # for controlled before/after comparison against the hybrid score alone
    argv = [a for a in argv if a not in ("--no-update", "--no-rerank")]
    n = 0
    if not no_update:
        meta, vectors, lines, n = update_index(VAULT, embed, meta, vectors, lines)
        if n or not os.path.exists(INDEX):
            _save(meta, vectors, lines)
        else:
            os.utime(INDEX, None)                                   # nothing changed: still mark the index as checked now, so callers judging its age do not refresh it again and again
    if "--update" in argv or not argv:
        print("index: %d lines, %d files re-embedded" % (len(lines), n))
        return 0
    q = " ".join(argv)
    rerank_fn = _rerank_fn() if lines and not no_rerank else None
    for s, p, l in search(q, lambda t: list(model.query_embed([t]))[0], vectors, lines, rerank_fn=rerank_fn):
        print("%s | %.2f | %s" % (p, s, l[:230]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
