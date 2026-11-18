# -*- coding: utf-8 -*-
"""A lightweight, Pi-style terminal harness for Kyogen (op a_4e656055 / a_c8ac581f, 2026-11-14):
"can u code a custom harness system like pi that u can launch in the terminal for the kyogen
project." Deliberately a thin interface, not new core logic -- reuses the exact same real
chat/coding-task/research functions already built and tested for the Discord bot
(kyogen_persona, kyogen_chat_tasks, kyogen_research), just without Discord, discord.py, or any
network client in the loop at all. The three-way classify-and-route decision (`handle_message`)
is factored out from the actual input()/print() REPL loop so it's unit-testable on its own.

Usage: python kyogen_terminal.py
"""
import os
import re
import sys
import time
import uuid

import pyfiglet
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

import kyogen_chat_tasks as kct
import kyogen_persona
import kyogen_research as kres
import swarm_meta_review as mr
import swarm_milestone1 as sm
from kyogen_shared_history import append_shared_turn, load_shared_turns

LESSONS_PATH = r"C:\LocalAI\swarm_memory\lessons.jsonl"

MAX_TURNS = 10
RESULT_POLL_SECONDS = 5
RESULT_POLL_TIMEOUT_SECONDS = 300  # 5 min -- a terminal REPL can just block and wait,
# no background-watch mechanism needed the way the async Discord bot required one.


def handle_message(user_text, history):
    """The real three-way decision, identical in spirit to discord_daemon_kyogen.py's
    on_message routing but synchronous (no Discord, no asyncio) -- classify as a real coding
    request, a real research request, or plain chat. Returns (reply_text, process_note):
    process_note is a real, honest artifact of what happened before the reply (currently the
    real generated code for a coding request, once the daemon's result includes it) -- None
    for chat/research, which have no real intermediate artifact worth showing. Added
    2026-11-14 after the owner's 5th ask for "agent thinking/code writing" as its own visible
    block, distinct from the final response -- a plain reply string had nothing to carry that
    in. `history` is the caller's running list of {"role", "content"} turns; this function
    reads it but never mutates the list the caller passed in (returns the turns to append
    instead), so a test can check exactly what would be added."""
    if kct.is_real_coding_request(user_text):
        return _handle_coding_request(user_text)
    if kres.is_research_request(user_text):
        return kres.answer_research_request(user_text), None
    return kyogen_persona.chat(user_text, history), None


def _handle_coding_request(user_text):
    """Same integrity chain as the Discord bridge: generate -> independent review -> ground-
    truth cross-check -> only then queue for real. The terminal just blocks and polls instead
    of needing a background watch task, since there's no event loop to share. Returns
    (reply_text, code): code is the real candidate the daemon generated and tested, or None
    when there's nothing real to show (rejected before queuing, or still running)."""
    result = kct.queue_real_chat_task(user_text)
    if not result["queued"]:
        return "tried that one but caught a real problem with my own first attempt before running it for real - not going to ship something i cant verify.", None
    job_name = result["job_name"]
    elapsed = 0
    while elapsed < RESULT_POLL_TIMEOUT_SECONDS:
        time.sleep(RESULT_POLL_SECONDS)
        elapsed += RESULT_POLL_SECONDS
        job_result = kct.find_result(job_name)
        if job_result is not None:
            code = job_result.get("code")
            if job_result.get("passed"):
                return "done, ref %s - tested it myself, it works." % job_name, code
            return "tried it (ref %s) but it didn't pass my own testing, not giving you something broken." % job_name, code
    return "%s is still running after %ds - longer than expected, check back on it later." % (job_name, elapsed), None


BANNER_ANSI_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kyogen_banner_ansi.txt")
# The real established logo, not a hand-drawn approximation: this is the actual lotus crest
# (chafa-rendered from kyogen_lotus_crest.png) + KYOGEN figlet wordmark, captured live from the
# rig's own MOTD script. the owner rejected a first hand-drawn ASCII lotus for exactly this
# reason: "please redo it properly with the properly made and established kyogen logo."
# Tried replacing this with a from-scratch native Pillow/Rich half-block renderer on
# 2026-11-14 after a false lead ("the one on the rig vs the one u put are different" turned
# out to be about PNG assets that were actually byte-identical everywhere) -- that rewrite
# produced a genuinely worse, colorless, unrecognizable render in his real terminal (he sent
# the correct reference screenshot straight from the rig and it was obviously right). Reverted
# back to this known-good approach the same session. Printed as raw ANSI (sys.stdout, not
# markup) since it already contains real escape codes, not Rich markup -- passing it through
# Rich's markup parser would misread the literal '[' characters in the escape sequences.


def _real_lesson_count(lessons_path=LESSONS_PATH):
    """Pure-ish (one real file read, reuses the already-tested loader). Never fabricates a
    number -- 0 if the file doesn't exist yet."""
    return len(mr.load_recent_lessons(lessons_path, n=100000))


def _capability_panel(session_id, lessons_path=None):
    """A real Hermes-Agent-style capability manifest at startup -- the owner's direct ask after
    seeing a real screenshot of it ("u see how its a proper agent terminal"). Lists Kyogen's
    REAL capabilities honestly (no invented tool names to look impressive): three real routes
    (chat, a verified coding-task pipeline, a narrow real lookup), the real model backing it,
    and the real current lesson count -- never a static or fabricated figure. `lessons_path`
    defaults to the real module-level LESSONS_PATH, read fresh at call time (not bound as a
    default argument) so a test override actually takes effect -- a real bug caught live in
    this module's own first test: a default argument bound at function-definition time
    doesn't see a later monkeypatch of the module attribute."""
    _, model_name = sm.ROLE_MODEL_URL.get("kyogen_chat", (sm.LOCAL_QWEN_URL, "qwen"))
    lesson_count = _real_lesson_count(lessons_path or LESSONS_PATH)
    body = (
        "[bold]Available Routes[/bold]\n"
        "  chat:      casual conversation (kyogen_persona)\n"
        "  code_task: generate -> independent review -> ground-truth check -> run for real (kyogen_chat_tasks)\n"
        "  research:  real factual lookup, encyclopedic facts only, no current events (kyogen_research)\n"
        "\n"
        "[bold]Session[/bold]\n"
        "  model:   %s (local)\n"
        "  lessons: %d real accumulated\n"
        "  session: %s"
    ) % (model_name, lesson_count, session_id)
    return Panel(body, title="[bold red]Kyogen[/bold red] [dim]local specialist-swarm[/dim]", border_style="red", padding=(1, 2))


def _estimate_tokens(text):
    """Pure. A rough, honestly-labeled estimate (chars/4, the common rough heuristic), never
    presented as an exact count -- no real tokenizer for this local model is installed here,
    and installing one (tiktoken, a different vocabulary anyway) just to show an approximate
    number isn't worth the dependency."""
    return max(1, len(text) // 4)


_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _banner_required_width(ansi_content):
    """Pure. The real logo+wordmark are printed side by side on single long lines (~146
    chars) -- a narrower console would make Rich's own line-wrapping interleave the two
    halves into a scrambled mess (a real bug caught live 2026-11-14 by actually looking at
    the rendered output, not assuming Text.from_ansi would just work). Measuring the real
    content's longest visible line (ANSI codes stripped) instead of hardcoding a number
    means this keeps working if the asset is ever re-captured at a different size."""
    return max((len(_ANSI_SGR_RE.sub("", line)) for line in ansi_content.split("\n")), default=0)


def _print_banner(console):
    # Real bug caught live 2026-11-14 ("theres 2 kyogen logos there"): the real ANSI capture
    # already contains its own wordmark (lotus + figlet KYOGEN side by side) -- printing a
    # SECOND pyfiglet title underneath it was redundant, not a second distinct element. Only
    # one logo now. (Also tried, then reverted the same day: a from-scratch native Pillow/Rich
    # half-block renderer -- it produced a genuinely worse, colorless render in his real
    # terminal; this captured-ANSI approach is the one he confirmed correct.)
    try:
        with open(BANNER_ANSI_PATH, encoding="utf-8") as f:
            ansi_content = f.read()
        # force_terminal=True/color_system="truecolor"/legacy_windows=False (real bug caught
        # live 2026-11-14, the owner sent side-by-side screenshots: his reference was red, this
        # came out plain white). Two real mechanisms found reproducing this locally: (1) Rich
        # auto-detects "legacy Windows console" on win32 and routes output through its own
        # Win32-API text path instead of plain ANSI escapes -- legacy_windows=False makes it
        # emit standard escapes like any other platform, which is what Windows Terminal (what
        # he's actually running) understands directly. (2) separately, if the real console's
        # active codepage isn't UTF-8 (`chcp 65001`, set by the launcher before python starts
        # -- Python latches its own encoding default at interpreter startup, so setting it
        # from inside this process is too late), printing a box-drawing or extended character
        # raises UnicodeEncodeError against cp1252 -- reproduced directly, it crashed the same
        # way his screenshot's terminal didn't, so catching it here is a real safety net, not
        # a hypothetical one.
        wide_console = Console(
            width=_banner_required_width(ansi_content), file=sys.stdout,
            color_system="truecolor", force_terminal=True, legacy_windows=False,
        )
        wide_console.print(Text.from_ansi(ansi_content), no_wrap=True, overflow="crop")
    except (OSError, UnicodeEncodeError):
        console.print(Text(pyfiglet.figlet_format("KYOGEN", font="block"), style="bold red"))
    session_id = uuid.uuid4().hex[:12]
    console.print(_capability_panel(session_id))
    console.print("\n[dim]Welcome to Kyogen. Type your message, or 'exit'/'quit' to leave.[/dim]\n")
    return session_id


def _status_line(model_name, lesson_count, extra=""):
    """The real bottom status bar, Hermes-style: model, real lesson count, and whatever
    per-turn info the caller has (elapsed time, token estimate, or a live 'thinking' marker)."""
    base = "[dim]%s[/dim] [red]·[/red] [dim]lessons: %d[/dim]" % (model_name, lesson_count)
    return base + ((" [red]·[/red] " + extra) if extra else "")


def _read_input_boxed(console, model_name, lesson_count):
    """A real bordered input area (the owner, live: "AND THERES NO TEXTBOX") -- Rich can't put
    a live cursor inside a Panel's own border, so this draws the box by hand: a top rule, the
    styled prompt line where the real input happens, then a bottom rule carrying the real
    status line, the same visual shape as Hermes's own input bar."""
    console.rule(style="red")
    try:
        user_text = console.input("[bold red]you[/] [red]›[/red] ").strip()
    except (EOFError, KeyboardInterrupt):
        console.rule(style="red")
        raise
    console.print(_status_line(model_name, lesson_count))
    console.rule(style="red")
    return user_text


def main():
    # force_terminal=True/color_system="truecolor"/legacy_windows=False: same real bug as the
    # banner (Rich's legacy-Windows-console render path, not real ANSI), applied to the
    # console every other panel/status/input line in the session uses, not just the banner.
    console = Console(color_system="truecolor", force_terminal=True, legacy_windows=False)
    # Real gap found live 2026-11-14 ("I NEED YOU TO MAKE SURE THE DC AND THE TERMINAL IS
    # SYNCED AND ARE THE SAME"): the same two real questions ("hi", "who are you?") asked in
    # each interface produced two completely independent conversations with different replies
    # -- this interface's history was in-memory only, never written anywhere the Discord bot
    # could see, and vice versa. Seeding from the one real shared file means starting the
    # terminal picks up wherever the Discord conversation actually left off.
    history = load_shared_turns()
    _print_banner(console)
    _, model_name = sm.ROLE_MODEL_URL.get("kyogen_chat", (sm.LOCAL_QWEN_URL, "qwen"))
    while True:
        try:
            user_text = _read_input_boxed(console, model_name, _real_lesson_count())
        except (EOFError, KeyboardInterrupt):
            console.print()
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            break
        started_at = time.time()
        token_estimate = _estimate_tokens(user_text)
        thinking_label = "[bold red]kyogen is thinking[/] [dim](%s · ~%d tok in)[/dim]" % (model_name, token_estimate)
        with console.status(thinking_label, spinner="dots"):
            reply_text, process_note = handle_message(user_text, list(history))
        elapsed = time.time() - started_at
        # Real gap found live 2026-11-14 (the owner, 5th ask): console.status is a transient
        # live region -- it vanishes the instant the with-block exits, leaving nothing behind
        # once the response prints. Printing this line afterward keeps a real, retained record
        # of the thinking phase in scrollback instead of it disappearing into the final answer.
        console.print("[dim]kyogen thought for %.1fs[/dim]" % elapsed)
        if process_note:
            console.print(Panel(
                process_note, title="[bold red]kyogen[/] [dim]code written[/dim]",
                border_style="red", title_align="left",
            ))
        reply_tokens = _estimate_tokens(reply_text)
        console.print(Panel(
            reply_text,
            title="[bold red]kyogen[/] [dim](%.1fs · ~%d tok out)[/dim]" % (elapsed, reply_tokens),
            border_style="red", title_align="left",
        ))
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": reply_text})
        history = history[-(MAX_TURNS * 2):]
        append_shared_turn(user_text, reply_text)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
