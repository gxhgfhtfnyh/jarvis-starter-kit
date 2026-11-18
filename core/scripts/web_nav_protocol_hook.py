# -*- coding: utf-8 -*-
"""
PreToolUse hook for the two Claude-in-Chrome "entry point" tools (navigate,
tabs_create_mcp) -- built 2026-08-19 after making the exact same tooling
mistake twice in one day (Chrome for the RP student portal when Playwright's
politemall profile might have already covered it, then Chrome for a YouTube
video when watch_video.py exists specifically for that). Both times the rule
already existed in writing; neither time did checking it actually happen
before reaching for Chrome out of habit.

Structural on purpose, same lesson as professional_mode_hook.py and the
avatar mood-hook mechanism already in this codebase: a habit that fires
automatically doesn't have the "forgot to check" failure mode a written
rule does. Doesn't block the call (Chrome is sometimes genuinely the right
tool) -- just forces the actual decision to get made consciously via
additionalContext, instead of skipped by momentum.

Usage (from a PreToolUse hook, matcher:
"mcp__claude-in-chrome__navigate|mcp__claude-in-chrome__tabs_create_mcp"):
    python web_nav_protocol_hook.py
"""
import json
import sys

REMINDER = (
    "[Before this Chrome call: check J:\\Jarvis-Brain\\05 - Resources\\"
    "Web Navigation Protocol.md's table for this site/task first. Video "
    "content specifically -> watch_video.py, not Chrome screenshots. "
    "RP/politemall-family sites -> try the politemall Playwright profile "
    "first. Chrome is the expensive fallback for a fresh login or when "
    "nothing else covers it, not the default reach.]"
)


def main():
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        },
        "additionalContext": REMINDER,
    }))


if __name__ == "__main__":
    main()
    sys.exit(0)
