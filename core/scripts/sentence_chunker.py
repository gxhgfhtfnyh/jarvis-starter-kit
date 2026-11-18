# -*- coding: utf-8 -*-
"""Splits a stream of incoming text (as it arrives from an LLM, token by token or chunk by chunk)
into complete sentences as soon as each one is safely finished, so a streaming TTS layer can start
synthesizing sentence 1 while the LLM is still generating sentence 2 onward -- the single biggest
latency win named in J:\\Jarvis-Brain\\05 - Resources\\Real-Time Voice + Better Cloning Research
(Nov 4 2026).md ("Stream, don't batch, at every stage").

Guards against false sentence boundaries: a decimal point between digits ("3.14"), a known
abbreviation ("Dr.", "Mr.", "vs.", "etc.", ...), and a run of dots (ellipsis "...") collapsing to
one boundary instead of three.

Pure core (unit tested): SentenceChunker.feed()/flush(). No I/O, no audio, no model calls here --
tts_stream.py wires this to the actual synthesis step.
"""
import re

# Abbreviations whose trailing "." must NOT be treated as a sentence end. Checked case-sensitively
# against the word immediately before the period (lowercased for the match).
ABBREVIATIONS = {
    "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st", "vs", "etc", "eg", "ie",
    "inc", "ltd", "co", "corp", "no", "vol", "fig", "approx", "dept", "est", "gov",
    "mt", "ft", "max", "min",
}

# A sentence-ending punctuation run, optionally followed by closing quotes/brackets, then whitespace.
_BOUNDARY_RE = re.compile(r'[.!?]+[\'")\]]*\s+')
_WORD_RE = re.compile(r'[A-Za-z]+$')


def _is_real_boundary(buf, punct_start):
    """True if the punctuation run starting at punct_start is a genuine sentence end, not a decimal
    point ("3.14") or a known abbreviation ("Dr."). Pure."""
    before = buf[:punct_start]
    if not before:
        return False
    if before[-1].isdigit():
        return False  # "3." followed by a digit-start punctuation run -> likely a decimal, don't split
    m = _WORD_RE.search(before)
    if m and m.group(0).lower() in ABBREVIATIONS:
        return False
    return True


class SentenceChunker:
    """feed(text_chunk) -> list of complete sentences newly finished by this chunk (0, 1, or several
    if the chunk contains multiple). flush() -> the final trailing fragment, call once the stream is
    known to be done (e.g. end of LLM response) so the last sentence (which may lack trailing
    whitespace) still gets spoken."""

    def __init__(self):
        self._buf = ""

    def feed(self, text_chunk):
        self._buf += text_chunk
        out = []
        pos = 0
        while True:
            m = _BOUNDARY_RE.search(self._buf, pos)
            if not m:
                break
            if not _is_real_boundary(self._buf, m.start()):
                pos = m.end()  # not a real boundary - keep scanning past it, same buffer
                continue
            out.append(self._buf[:m.end()].strip())
            self._buf = self._buf[m.end():]
            pos = 0
        return out

    def flush(self):
        rest = self._buf.strip()
        self._buf = ""
        return rest
