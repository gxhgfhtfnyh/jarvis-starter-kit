# -*- coding: utf-8 -*-
"""
Jarvis's port of the "brain.js" deterministic retrieval layer from the
RoboNuggets Second Brain guide (the PDF in Downloads/Referencesss, read
2026-08-13, same system as the reference video): evidence fetching with
ZERO model calls. Strip the question to keywords -> score every indexed
file WITHOUT opening it -> open only the best file(s) -> extract only the
relevant sections -> print one capped evidence block. The model then gets
invoked once, reading that block, instead of chaining Glob/Grep/Read
across the whole vault.

Index lives at J:\\Jarvis2\\logs\\brain_index.json -- auto-rebuilds when
missing or when any indexed file's mtime is newer than the index's build
time (a cheap stat-only scan, no reads), or on demand with --rebuild.
Content-based, not a fixed time window: a note edited 5 minutes ago
triggers a rebuild on the very next query instead of waiting out a full
day (found stale 2026-08-13 -- tonight's daily-note edits sat unindexed
for 90+ minutes, well inside the old 24h window, because nothing had
actually re-checked whether the vault had changed).

Usage:
    python brain.py "what did we decide about the telegram daemon"
    python brain.py --rebuild
"""
import json
import math
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"J:\Jarvis2\scripts")
from vault_link_graph import LINK_RE

VAULT_ROOT = Path(r"J:\Jarvis-Brain")
SCRIPTS_ROOT = Path(r"J:\Jarvis2\scripts")
INDEX_PATH = Path(r"J:\Jarvis2\logs\brain_index.json")
SKIP_DIRS = {".obsidian", ".git", "__pycache__"}

EVIDENCE_CAP = 9_000  # same 9KB evidence cap the reference system uses
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)

STOPWORDS = {
    "the", "and", "for", "are", "was", "were", "what", "when", "where",
    "which", "who", "how", "why", "did", "does", "done", "has", "have",
    "had", "its", "it's", "with", "that", "this", "these", "those", "about",
    "into", "from", "our", "you", "your", "his", "her", "their", "there",
    "then", "than", "but", "not", "can", "could", "should", "would", "will",
    "just", "like", "get", "got", "make", "made", "any", "all", "some",
    "one", "two", "way", "also", "use", "used", "using",
}


def tokenize(text):
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


def _index_one(path, kind, file_id):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    headings = [m.group(2).strip() for m in HEADING_RE.finditer(text)] if kind == "note" else []
    links = [t.strip() for t in LINK_RE.findall(text)] if kind == "note" else []
    words = Counter(tokenize(text))
    return {
        "id": file_id,
        "path": str(path),
        "kind": kind,
        "title": path.stem,
        "headings": headings,
        "links": links,
        # total BEFORE trimming -- needed for BM25-style length normalization (score_entry), lost if
        # computed from the trimmed dict below
        "total_words": sum(words.values()),
        # trimmed per-file term counts -- enough for scoring, keeps the
        # whole index loadable in one small read
        "words": dict(words.most_common(500)),
    }


def build_index():
    entries = []
    for p in VAULT_ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        e = _index_one(p, "note", str(p.relative_to(VAULT_ROOT)).replace("\\", "/"))
        if e:
            entries.append(e)
    for p in sorted(SCRIPTS_ROOT.iterdir()):
        if p.is_file() and p.suffix in (".py", ".ps1"):
            e = _index_one(p, "script", p.name)
            if e:
                entries.append(e)
    INDEX_PATH.parent.mkdir(exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps({"built": time.time(), "entries": entries}, ensure_ascii=False),
        encoding="utf-8",
    )
    return entries


def _vault_signature():
    """Cheap staleness check: the newest mtime across every file the index
    would cover, no file reads -- same idiom as graph_bridge.py's own
    signature watcher. Compared against the index's build time so ANY
    change since the last build triggers a rebuild, not just a fixed
    calendar window."""
    latest = 0.0
    for p in VAULT_ROOT.rglob("*.md"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            latest = max(latest, p.stat().st_mtime)
        except OSError:
            pass
    for p in SCRIPTS_ROOT.iterdir():
        if p.is_file() and p.suffix in (".py", ".ps1"):
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                pass
    return latest


def load_index():
    if INDEX_PATH.exists():
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            if _vault_signature() <= data.get("built", 0):
                return data["entries"]
        except (json.JSONDecodeError, OSError):
            pass
    return build_index()


def doc_freq(entries):
    """How many indexed files contain each token at least once -- for IDF weighting."""
    df = Counter()
    for e in entries:
        df.update(e["words"].keys())
    return df


def idf_weight(token, df, n_docs):
    """Smoothed IDF: a token present in every file weighs about 1x; a rare token weighs much more.
    The +1 smoothing keeps this positive even for a token in literally every doc."""
    return math.log((n_docs + 1) / (df.get(token, 0) + 1)) + 1


BM25_K1 = 1.5
BM25_B = 0.75


def _bm25_tf(count, doclen, avgdl, k1=BM25_K1, b=BM25_B):
    """BM25's term-frequency/length-normalization piece: a raw count means more in a short document than
    the same raw count in a long one, and repeats of the same word saturate instead of accumulating
    without bound. avgdl<=0 (no corpus stats yet) falls back to the raw count, unnormalized."""
    if avgdl <= 0:
        return count
    return (count * (k1 + 1)) / (count + k1 * (1 - b + b * (doclen / avgdl)))


def score_entry(entry, tokens, df=None, n_docs=1, avgdl=0):
    """Score WITHOUT opening the file -- index data only. Pass df/n_docs (from doc_freq/len(entries)) to
    downweight common words (e.g. "machine", "runs", "now"), and avgdl (average total_words across the
    corpus) for BM25-style length normalization, so a long note that merely repeats common words a lot
    doesn't beat a short note stating the exact fact once -- real bug found 2026-11-17: a verbatim-phrased
    fact in Current Facts (verified).md lost to an unrelated long devlog note, because raw per-token counts
    (capped but otherwise unnormalized) just reward any document long enough to hit the cap on every token,
    regardless of what fraction of that document is actually about the query."""
    score = 0.0
    title_toks = set(tokenize(entry["title"]))
    heading_toks = set()
    for h in entry["headings"]:
        heading_toks.update(tokenize(h))
    link_toks = set()
    for l in entry["links"]:
        link_toks.update(tokenize(l))
    words = entry["words"]
    doclen = entry.get("total_words", sum(words.values())) or 1
    for t in tokens:
        w = idf_weight(t, df, n_docs) if df is not None else 1.0
        if t in title_toks:
            score += 6 * w
        if t in heading_toks:
            score += 3 * w
        if t in link_toks:
            score += 2 * w
        score += _bm25_tf(words.get(t, 0), doclen, avgdl) * w
    return score


def extract_evidence(entry, tokens, budget):
    """Open ONLY this file, return only its relevant sections."""
    try:
        text = Path(entry["path"]).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    if entry["kind"] == "script":
        # docstring + hit lines with context beats whole-file dumps
        lines = text.splitlines()
        keep = set(range(min(len(lines), 15)))  # header/docstring region
        for i, line in enumerate(lines):
            low = line.lower()
            if any(t in low for t in tokens):
                keep.update(range(max(0, i - 2), min(len(lines), i + 3)))
        out, last = [], -10
        for i in sorted(keep):
            if i > last + 1:
                out.append("    …")
            out.append(lines[i])
            last = i
        return "\n".join(out)[:budget]

    # note: split on headings, keep sections that contain query tokens
    parts = HEADING_RE.split(text)
    # parts = [pre, hashes1, title1, body1, hashes2, title2, body2, ...]
    sections = []
    if parts[0].strip():
        sections.append(("(intro)", parts[0]))
    for i in range(1, len(parts) - 2, 3):
        sections.append((parts[i + 1].strip(), parts[i + 2]))

    scored = []
    for title, body in sections:
        low = (title + " " + body).lower()
        hits = sum(low.count(t) for t in tokens)
        if hits:
            scored.append((hits, title, body))
    scored.sort(key=lambda s: -s[0])

    out = []
    used = 0
    for hits, title, body in scored:
        chunk = f"## {title}\n{body.strip()}\n"
        if used + len(chunk) > budget:
            chunk = chunk[: max(0, budget - used)]
        out.append(chunk)
        used += len(chunk)
        if used >= budget:
            break
    return "\n".join(out)


def ask(question):
    tokens = tokenize(question)
    if not tokens:
        print("(question reduced to no usable keywords)")
        return
    entries = load_index()
    df = doc_freq(entries)
    n_docs = len(entries)
    avgdl = (sum(e.get("total_words", 0) for e in entries) / n_docs) if n_docs else 0
    ranked = sorted(entries, key=lambda e: -score_entry(e, tokens, df, n_docs, avgdl))
    top = [e for e in ranked[:5] if score_entry(e, tokens, df, n_docs, avgdl) > 0]
    if not top:
        print("(no indexed file matches those keywords)")
        return

    print(f"KEYWORDS: {' '.join(tokens)}")
    print("CANDIDATES:")
    for e in top:
        print(f"  {score_entry(e, tokens, df, n_docs, avgdl):>6.1f}  {e['kind']:<6} {e['id']}")
    print()

    budget = EVIDENCE_CAP
    for e in top[:2]:  # best file, plus runner-up if budget remains
        if budget <= 500:
            break
        ev = extract_evidence(e, tokens, budget)
        if ev.strip():
            print(f"===== EVIDENCE: {e['id']} =====")
            print(ev.strip())
            print()
            budget -= len(ev)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--rebuild" in sys.argv:
        entries = build_index()
        print(f"index rebuilt: {len(entries)} files -> {INDEX_PATH}")
    elif len(sys.argv) > 1:
        ask(" ".join(sys.argv[1:]))
    else:
        print('Usage: python brain.py "your question"  |  python brain.py --rebuild')
