# -*- coding: utf-8 -*-
"""
Semantic (meaning-based) search across the vault, using local embeddings
(fastembed - lightweight, ONNX-based, no torch) instead of literal Grep/Glob
keyword matching. Useful once the vault gets large enough that "what did we decide
about X" doesn't map cleanly onto exact keywords.

The embedding model downloads once (~100MB, cached locally by fastembed) on first
use, then runs fully offline - no API calls, no per-query cost.

Index storage: a single JSON file (J:\\Jarvis2\\vault_index.json) mapping chunk ->
embedding vector + source note + heading + the source file's mtime (enables
incremental rebuilds - see build_index()).

Usage:
    python vault_search.py build          # (re)build the index - incremental by default
    python vault_search.py build --full   # force a full rebuild, ignoring cached mtimes
    python vault_search.py search "<query>" [--k N]
    python vault_search.py classify "<path-to-note>"   # suggest project/type from neighbors
    python vault_search.py oversized [--chars N]        # flag notes big enough to risk context bloat

As a library:
    from vault_search import search
    results = search("what did we decide about the smart home security gap", k=5)
"""
import json
import re
import sys
from pathlib import Path

VAULT_DIR = Path(r"J:\Jarvis-Brain")
INDEX_PATH = Path(r"J:\Jarvis2\vault_index.json")
SKIP_DIRS = {".obsidian", ".git"}
MODEL_NAME = "BAAI/bge-small-en-v1.5"

# Course-code / topic synonyms so a query like "chem" or "genetics" finds the
# right course note even without the exact code - built 2026-08-08.
QUERY_EXPANSIONS = {
    "chem": "A364 Analytical Chemistry",
    "chemistry": "A364 Analytical Chemistry",
    "genetics": "A232 Genetics",
    "immunology": "A231 Immunology",
    "immuno": "A231 Immunology",
    "cell culture": "A241 Cell Culture",
    "rdna": "A245 Recombinant DNA Technologies",
    "recombinant dna": "A245 Recombinant DNA Technologies",
    "food": "A244 Microbial and Cellular Applications in Food and Health",
    "microbial": "A244 Microbial and Cellular Applications in Food and Health",
}

OVERSIZED_CHARS = 8000  # a note beyond this is worth proactively summarizing


def _get_model():
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL_NAME)


def _chunk_note(text, max_chars=1500):
    """Splits a note into chunks by ## headings first (keeps related content
    together), falling back to fixed-size splitting for very long sections."""
    sections = re.split(r"(?=^## )", text, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) <= max_chars:
            chunks.append(section)
        else:
            for i in range(0, len(section), max_chars):
                chunks.append(section[i:i + max_chars])
    return chunks or [text[:max_chars]]


def expand_query(query):
    """Appends known synonym expansions found in the query - doesn't replace
    the original text, just adds context so embedding similarity has more to
    match against."""
    lower = query.lower()
    additions = [full for key, full in QUERY_EXPANSIONS.items() if key in lower]
    if additions:
        return query + " (" + ", ".join(dict.fromkeys(additions)) + ")"
    return query


def build_index(full_rebuild=False):
    md_files = [p for p in VAULT_DIR.rglob("*.md") if not any(part in SKIP_DIRS for part in p.parts)]

    # Load existing index for incremental reuse, unless a full rebuild is forced.
    existing_by_note = {}
    if not full_rebuild and INDEX_PATH.exists():
        try:
            old_data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            for e in old_data.get("entries", []):
                existing_by_note.setdefault(e["note"], []).append(e)
        except (json.JSONDecodeError, KeyError):
            existing_by_note = {}

    model = None  # lazy-loaded only if something actually needs embedding
    entries = []
    to_embed_chunks = []
    to_embed_meta = []
    reused, rebuilt = 0, 0

    for path in md_files:
        rel_path = str(path.relative_to(VAULT_DIR))
        mtime = path.stat().st_mtime
        cached = existing_by_note.get(rel_path)
        if cached and all(e.get("mtime") == mtime for e in cached):
            entries.extend(cached)
            reused += 1
            continue

        rebuilt += 1
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for chunk in _chunk_note(text):
            to_embed_chunks.append(chunk)
            to_embed_meta.append({"note": rel_path, "preview": chunk[:150], "mtime": mtime, "chars": len(chunk)})

    if to_embed_chunks:
        model = _get_model()
        BATCH = 16
        embeddings = []
        for i in range(0, len(to_embed_chunks), BATCH):
            batch = to_embed_chunks[i:i + BATCH]
            embeddings.extend(list(model.embed(batch, batch_size=BATCH)))
            print(f"  Embedded {min(i + BATCH, len(to_embed_chunks))}/{len(to_embed_chunks)} chunks...")
        for meta, emb in zip(to_embed_meta, embeddings):
            entries.append({**meta, "vector": emb.tolist()})

    if not entries:
        print("No notes found to index.")
        return

    INDEX_PATH.write_text(json.dumps({"model": MODEL_NAME, "entries": entries}, ensure_ascii=False), encoding="utf-8")
    print(f"Indexed {len(entries)} chunks from {len(md_files)} notes -> {INDEX_PATH} "
          f"({reused} files reused unchanged, {rebuilt} re-embedded)")


def search(query, k=5, expand=True, multi_hop=False):
    if not INDEX_PATH.exists():
        raise RuntimeError("No index found - run build_index() / `python vault_search.py build` first.")

    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    entries = data["entries"]

    model = _get_model()
    search_query = expand_query(query) if expand else query
    query_vec = list(model.embed([search_query]))[0]

    import numpy as np
    vectors = np.array([e["vector"] for e in entries])
    query_arr = np.array(query_vec)
    sims = vectors @ query_arr / (np.linalg.norm(vectors, axis=1) * np.linalg.norm(query_arr) + 1e-9)

    top_idx = np.argsort(-sims)[:k]
    results = [{"note": entries[i]["note"], "preview": entries[i]["preview"], "score": float(sims[i])} for i in top_idx]

    if multi_hop:
        # Pull in one hop of wikilinked notes from the top result's own chunk
        # text, so a query that lands on a note pointing elsewhere doesn't
        # dead-end at the flat top-k.
        top_note_path = VAULT_DIR / entries[top_idx[0]]["note"]
        try:
            top_text = top_note_path.read_text(encoding="utf-8")
            linked_names = set(re.findall(r"\[\[([^\]|#]+)", top_text))
            seen_notes = {r["note"] for r in results}
            for e in entries:
                note_stem = Path(e["note"]).stem
                if note_stem in linked_names and e["note"] not in seen_notes:
                    results.append({"note": e["note"], "preview": e["preview"], "score": None, "via_link": True})
                    seen_notes.add(e["note"])
        except OSError:
            pass

    return results


def suggest_classification(note_path, k=5):
    """Given a note (existing or draft), find its nearest neighbors by content
    and suggest a project/type based on what they use - helps classify a new
    note consistently instead of guessing, and flags likely duplicates."""
    text = Path(note_path).read_text(encoding="utf-8") if Path(note_path).exists() else note_path
    results = search(text[:1500], k=k, expand=False)

    from collections import Counter
    fm_re = re.compile(r"^---\n(.*?)\n---", re.DOTALL)
    projects, types = Counter(), Counter()
    for r in results:
        full_path = VAULT_DIR / r["note"]
        if not full_path.exists():
            continue
        m = fm_re.match(full_path.read_text(encoding="utf-8", errors="replace"))
        if not m:
            continue
        for line in m.group(1).splitlines():
            if line.startswith("project:"):
                projects[line.split(":", 1)[1].strip()] += 1
            if line.startswith("type:"):
                types[line.split(":", 1)[1].strip()] += 1

    return {
        "similar_notes": results,
        "suggested_project": projects.most_common(1)[0][0] if projects else None,
        "suggested_type": types.most_common(1)[0][0] if types else None,
    }


def check_before_creating(draft_text, warn_threshold=0.80, k=3):
    """Call before writing a brand-new note - checks whether something close
    enough already exists that it should be extended instead (VAULT-INDEX.md's
    own rule: "Append before you create... fewer, fuller notes beat many thin
    ones"). Returns the close matches if any are found above threshold, empty
    list if it's genuinely new ground."""
    results = search(draft_text[:1500], k=k, expand=False)
    return [r for r in results if r["score"] and r["score"] >= warn_threshold]


def find_oversized(char_threshold=OVERSIZED_CHARS):
    if not INDEX_PATH.exists():
        raise RuntimeError("No index found - run build first.")
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    by_note = {}
    for e in data["entries"]:
        by_note[e["note"]] = by_note.get(e["note"], 0) + e.get("chars", len(e.get("preview", "")))
    return sorted([(note, size) for note, size in by_note.items() if size > char_threshold],
                  key=lambda x: -x[1])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if len(sys.argv) < 2:
        print("Usage: python vault_search.py build [--full] | search \"<query>\" [--k N] | classify \"<path>\" | oversized [--chars N]")
        sys.exit(1)

    if sys.argv[1] == "build":
        build_index(full_rebuild="--full" in sys.argv)
    elif sys.argv[1] == "search":
        query = sys.argv[2]
        k = 5
        if "--k" in sys.argv:
            k = int(sys.argv[sys.argv.index("--k") + 1])
        for r in search(query, k=k):
            print(f"[{r['score']:.3f}] {r['note']}")
            print(f"    {r['preview']}")
    elif sys.argv[1] == "classify":
        result = suggest_classification(sys.argv[2])
        print(f"Suggested project: {result['suggested_project']}, type: {result['suggested_type']}")
        print("Similar notes:")
        for r in result["similar_notes"]:
            print(f"  [{r['score']:.3f}] {r['note']}")
    elif sys.argv[1] == "oversized":
        threshold = OVERSIZED_CHARS
        if "--chars" in sys.argv:
            threshold = int(sys.argv[sys.argv.index("--chars") + 1])
        for note, size in find_oversized(threshold):
            print(f"{size:>7} chars  {note}")
    else:
        print("Unknown command. Use 'build', 'search', 'classify', or 'oversized'.")
