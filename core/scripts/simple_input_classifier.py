# -*- coding: utf-8 -*-
"""Pure classifier: decides whether a heard utterance is clearly simple enough to answer
entirely from the prerendered voice bank, no real LLM+TTS round trip needed at all -- the owner's
refined ask, 2026-11-05: "it should be capable enough to answer simple questions or start a
natural answer while your full answer generates ykwim.... you shouldnt inturrupt yourself and
the clips should play seamlessly". This module is the "answer simple questions [directly]" half
of that; the grace-window filler-then-real-answer behaviour in voice_loop.py's
run_loop_live_handoff() is the other half ("start a natural answer... while your full answer
generates").

DESIGN -- conservative, closed-set, whole-utterance matching only:
the owner's own framing ("keep this classifier simple and conservative... only intercept CLEARLY
simple inputs... not try to be clever about borderline cases -- when in doubt, fall through to
the real pipeline") rules out anything fuzzy. So: a small number of closed phrase sets (bare
greetings, bare thanks, presence checks, bare yes/no/basic acknowledgments, bare farewells),
matched against the FULL normalized heard_text, not a substring/keyword search over a longer
sentence. "thanks for explaining that, can you also check the logs" is NOT classified as
simple (the normalized string isn't literally in the thanks set) even though it starts with a
bank word -- it falls through to the real LLM pipeline, which is the correct call since it
carries a real follow-up request a canned line can't answer.

Each matched category also carries `keywords`: a filter voice_line_bank.select_line_filtered()
uses to prefer specific lines within that bank category whose filename genuinely reads as a fit
for the input, instead of a uniformly random pick off the whole category (e.g. the
"acknowledgment" category holds 112 lines spanning "on it"/"working on it" task-acks, "i'm
there"/"i hear you" presence-acks, and "no problem"/"of course" thanks-acks all together -- a
presence check should bias towards the second group, not land on "on it" by chance). See the
per-rule comments below for how each keyword set was picked by reading the real bank content
(C:\\LocalAI\\voice\\line_bank\\index.json), not just the category label -- the bank category
named "confirming" turned out to hold fact-check lines ("that's correct", "all checks out"),
not presence confirmations, so presence checks route to "acknowledgment" instead, where the
real "i'm there" lines actually live.
"""
import re

# -- phrase sets: the FULL normalized heard_text must equal one of these entries exactly. --

GREETINGS = frozenset({
    "hey", "hi", "hello", "yo", "hiya", "howdy", "sup",
    "hey there", "hi there", "hello there", "oh hey",
    "what's up", "whats up", "hey what's up", "hey whats up",
    "good morning", "morning", "good evening", "evening", "good afternoon",
    "hey jarvis", "hi jarvis", "hello jarvis", "yo jarvis",
})

THANKS = frozenset({
    "thanks", "thank you", "thanks a lot", "thank you so much", "thanks so much",
    "appreciate it", "really appreciate it", "much appreciated", "appreciated",
    "thanks jarvis", "thank you jarvis", "ty", "thx",
})

PRESENCE_CHECKS = frozenset({
    "you there", "you there?", "you still there", "are you there", "are you still there",
    "you around", "you listening", "still with me", "are you with me", "you with me",
})

YES_NO_ACKS = frozenset({
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "alright",
    "no", "nope", "nah",
    "got it", "cool", "nice", "sounds good", "sounds good to me",
    "makes sense", "fair enough", "fine", "alright then",
})

FAREWELLS = frozenset({
    "bye", "goodbye", "bye bye", "see ya", "see you", "later",
    "talk later", "talk to you later", "gotta go", "good night", "night",
    "catch you later", "see you later",
})

# (phrase set, bank category, keyword filter) -- keyword filter is None when the whole bank
# category is already on-topic (greeting, farewell); otherwise it narrows select_line_filtered()
# to the subset of that category's real lines that actually read as the right kind of reply.
_CATEGORY_RULES = (
    (GREETINGS, "greeting", None),
    # "i'm there" / "yep, heard you" / "i hear you" / "i'm following" / "i'm with you" -- the
    # real presence-acknowledging lines inside the much bigger "acknowledgment" category.
    (PRESENCE_CHECKS, "acknowledgment",
     ("i m there", "heard you", "hear you", "following", "with you")),
    # "no problem" / "no worries" / "no sweat" / "of course" -- the real "you're welcome"-
    # flavored lines in "acknowledgment" (most of that category is task-acks like "on it").
    (THANKS, "acknowledgment", ("no problem", "no worries", "no sweat", "of course")),
    # generic short conversational acks that fit replying to a bare "ok"/"yeah"/"got it" from
    # him, as opposed to the task-ack phrasing ("on it", "handled") meant for replying to a
    # request -- "yes"/"no" themselves don't get bank lines here (replying "no problem" to a
    # bare "no" would read wrong), so this intentionally falls back to the full category pool
    # for those two if none of these keywords match; see select_line_filtered()'s fallback.
    (YES_NO_ACKS, "acknowledgment",
     ("sounds good", "alright", "fair enough", "noted", "understood", "mm hm", "uh huh",
      "yeah", "for sure")),
    (FAREWELLS, "farewell", None),
)

_TRAILING_PUNCT_RE = re.compile(r"[?!.,;:]+$")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize(text):
    """-> lowercased, trailing-punctuation-stripped, whitespace-collapsed text. Pure. Leading
    punctuation is left alone on purpose -- STT output doesn't produce it, and over-stripping
    risks quietly matching things that weren't actually said."""
    if not text:
        return ""
    t = text.strip().lower()
    t = _TRAILING_PUNCT_RE.sub("", t).strip()
    t = _WHITESPACE_RE.sub(" ", t)
    return t


def classify_simple_input(heard_text):
    """-> {"category": str, "tone": None, "keywords": tuple or None, "matched_phrase": str} if
    heard_text is clearly simple enough to answer entirely from the prerendered voice bank (no
    LLM call needed at all), else None (meaning: fall through to the real LLM+TTS pipeline).

    Pure -- no disk/bank access here. Callers combine this with
    voice_line_bank.select_line_filtered() to turn the classification into an actual file path;
    this function only ever decides yes/no + which bucket, never picks or touches a specific
    line itself.

    Deliberately context-free: it looks only at this one heard_text, not conversation history.
    That's a real, accepted limitation for the yes/no/basic-acknowledgment bucket specifically
    (a bare "no" immediately after Jarvis asks a real substantive question would still get
    bank-answered) -- in scope because the owner named "basic yes/no" as one of his own examples
    of what should bank-answer, not an oversight.
    """
    normalized = normalize(heard_text)
    if not normalized:
        return None
    for phrases, category, keywords in _CATEGORY_RULES:
        if normalized in phrases:
            return {
                "category": category,
                "tone": None,
                "keywords": keywords,
                "matched_phrase": normalized,
            }
    return None
