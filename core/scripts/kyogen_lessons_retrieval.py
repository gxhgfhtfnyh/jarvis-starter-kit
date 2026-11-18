# -*- coding: utf-8 -*-
"""Real retrieval-augmented lesson lookup for Kyogen (2026-11-14, op a_4e656055) -- replaces
`kyogen_persona.py`'s old static "last N lessons" prompt-stuffing with real per-task relevance,
per the research dispatched the same night (vault: "Kyogen M3 Fine-Tune Feasibility") and
the owner's own go-ahead ("ill leave that project to u"): a real LoRA fine-tune doesn't fit this
hardware (bitsandbytes can't quantize this model's MoE experts, the one MoE-safe method needs
63-74GB VRAM against 28GB combined here), but real retrieval is both more feasible AND the
better-evidenced mechanism for this exact kind of narrow, continuously-growing, pattern-
correction knowledge (Ovadia et al.'s fine-tune-vs-RAG study; Reflexion/ExpeL's zero-gradient
experience retrieval; Cognition's "Devin: Memory and Dreaming" at this same problem shape).

Deliberately NOT an embedding-model + vector-database build: at this corpus size (~200 lessons,
growing a few/hour), even a brute-force similarity pass runs in milliseconds, so the research's
own conclusion applies -- store choice is a minor implementation detail at this scale, not a
bottleneck. This uses plain TF-IDF cosine similarity (no external dependency, no model download,
fully deterministic and unit-testable) rather than a learned embedding model, since the lessons
are short, keyword-dense technical English, and a pure lexical match is enough to find "a lesson
about the same kind of thing this task is about" without adding a new ML dependency for ~200
short documents.

Usage: `retrieve(query_text, lessons, k=5, min_score=MIN_SCORE)` is the pure core (unit tested).
`load_and_retrieve(query_text, lessons_path, k=5)` is the real-file-reading wrapper
`kyogen_persona.py` actually calls. Returns [] (not a forced fallback) when nothing is genuinely
relevant -- showing nothing for a turn is more honest than injecting an unrelated lesson just to
have something to show, same "don't invent filler" rule the old static version already followed."""
import json
import math
import re
from collections import Counter

TOKEN_RX = re.compile(r"[a-z0-9]+")

# Below this cosine-similarity score, a lesson isn't genuinely relevant to the query -- showing
# it would just be noise with extra steps, the same failure mode as the old static summary.
MIN_SCORE = 0.08

# Found live, not assumed: a totally unrelated query ("what is your favorite color") still
# scored a nonzero match against real lessons purely through "is"/"your" -- idf alone does NOT
# zero out a common function word unless it appears in literally every document, and a word in
# e.g. 80% of a few-hundred-document corpus still carries enough weight to create a spurious
# match. A real stopword filter is needed after all, not optional polish.
STOPWORDS = frozenset(
    "a an and are as at be by for from has have he in is it its of on or that the this to was "
    "were will with your you i what which who whom do does did not no so if than then but can "
    "could would should my me we us our they them their".split()
)


def tokenize(text):
    """Pure. Lowercase word tokens with common English function words filtered out, so a query
    can't spuriously match a lesson through shared stopwords alone -- only real content words
    drive similarity."""
    return [t for t in TOKEN_RX.findall((text or "").lower()) if t not in STOPWORDS]


def _idf(doc_tokens_list):
    """Pure. {term: inverse-document-frequency} across the real corpus -- standard smoothed idf
    so a term appearing in every document doesn't get a negative or zero weight."""
    n_docs = len(doc_tokens_list)
    doc_freq = Counter()
    for tokens in doc_tokens_list:
        doc_freq.update(set(tokens))
    return {term: math.log((1 + n_docs) / (1 + df)) + 1.0 for term, df in doc_freq.items()}


def _tfidf_vector(tokens, idf):
    """Pure. {term: tf*idf} for one document's tokens; terms absent from the corpus (idf
    unknown, e.g. words only in the live query) get weight 0 -- they can't match anything
    anyway since no lesson contains them either."""
    tf = Counter(tokens)
    return {term: count * idf.get(term, 0.0) for term, count in tf.items()}


def _cosine(vec_a, vec_b):
    """Pure. Standard cosine similarity between two sparse {term: weight} vectors."""
    shared = set(vec_a) & set(vec_b)
    dot = sum(vec_a[t] * vec_b[t] for t in shared)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve(query_text, lessons, k=5, min_score=MIN_SCORE):
    """Pure. `lessons` is a list of dicts each with a "lesson" text field (the real shape in
    lessons.jsonl). Returns up to k lesson dicts genuinely relevant to `query_text`, ranked by
    real cosine similarity, filtered below `min_score` -- [] when nothing clears the bar rather
    than forcing in the top-k regardless of how unrelated they actually are."""
    texts = [l.get("lesson", "") for l in lessons]
    doc_tokens = [tokenize(t) for t in texts]
    idf = _idf(doc_tokens)
    doc_vectors = [_tfidf_vector(tokens, idf) for tokens in doc_tokens]
    query_vector = _tfidf_vector(tokenize(query_text), idf)
    scored = [(_cosine(query_vector, dv), lesson) for dv, lesson in zip(doc_vectors, lessons)]
    scored = [(score, lesson) for score, lesson in scored if score >= min_score]
    scored.sort(key=lambda t: -t[0])
    return [lesson for _score, lesson in scored[:k]]


def load_and_retrieve(query_text, lessons_path, k=5, min_score=MIN_SCORE):
    """The real-file-reading wrapper kyogen_persona.py calls. [] if the lessons file doesn't
    exist yet or is empty -- never raises, same "no real lessons yet" honesty the old static
    summary already had."""
    try:
        with open(lessons_path, encoding="utf-8") as f:
            lessons = [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []
    return retrieve(query_text, lessons, k=k, min_score=min_score)
