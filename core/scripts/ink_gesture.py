# -*- coding: utf-8 -*-
"""Circling something on screen while talking - the geometric core, ported from Roamies'
voice-win/src/ink.rs (github.com/KenKaiii/Roamies, AGPL-3.0, read 2026-11-10 to build our
own version of the same gesture: hold a hotkey, speak, draw a loop around something on
screen, and that becomes visual context for the question).

This module is the pure geometry only (add_point/closing/encloses/capture_box/marks),
same constants and same pass/fail behavior as ink.rs, verified against the same test
cases as its own `which_paths_count_as_a_circled_area` test. The cursor-tracking loop,
overlay rendering, and screen capture (ink.rs's `Ink` struct and `imp` module) are
Windows-specific plumbing to build separately on top of this, once this core is trusted.
"""
import math

Pt = tuple

CLOSE_GAP = 22.0
MIN_RUN = 110.0
MIN_SIDE = 26.0
MIN_FILL = 0.3
START_AFTER = 6.0
PAUSE_ENDS = 0.35
PEN = 2.8


def add_point(pts, p):
    """Appends p to pts, smoothed a little (takes the jitter out without lagging behind
    the pointer). Returns False when it was too close to the last point to count."""
    if not pts:
        pts.append(p)
        return True
    last = pts[-1]
    if math.hypot(p[0] - last[0], p[1] - last[1]) < 2.0:
        return False
    pts.append((last[0] + (p[0] - last[0]) * 0.65, last[1] + (p[1] - last[1]) * 0.65))
    return True


def encloses(q):
    """Big enough both ways, and its outline holds a fair share of its box (a closed
    zigzag or a sliver doesn't)."""
    if len(q) < 3:
        return False
    x0 = min(p[0] for p in q)
    x1 = max(p[0] for p in q)
    y0 = min(p[1] for p in q)
    y1 = max(p[1] for p in q)
    w, h = x1 - x0, y1 - y0
    if w < MIN_SIDE or h < MIN_SIDE:
        return False
    n = len(q)
    a = sum(q[k][0] * q[(k + 1) % n][1] - q[(k + 1) % n][0] * q[k][1] for k in range(n))
    return abs(a) / 2.0 >= MIN_FILL * w * h


def closing(p, gap):
    """Where the loop the path has just closed starts (an index into p), or None: its
    newest point comes back near an earlier one, round a shape with room inside it. A
    line, a line drawn back over itself, a zigzag or a tiny scribble never closes."""
    if len(p) < 8:
        return None
    end = p[-1]
    run = 0.0
    i = len(p) - 1
    while i > 0:
        run += math.hypot(p[i][0] - p[i - 1][0], p[i][1] - p[i - 1][1])
        i -= 1
        if run >= MIN_RUN and math.hypot(p[i][0] - end[0], p[i][1] - end[1]) <= gap and encloses(p[i:]):
            return i
    return None


def capture_box(loops, screen):
    """The part of the screen to send for these loops: round them with 48 points to
    spare, at least 320x220, and on their screen (screen: x, y, w, h in points)."""
    pts = [p for loop in loops for p in loop]
    if not pts:
        return None
    x0 = min(p[0] for p in pts) - 48.0
    x1 = max(p[0] for p in pts) + 48.0
    y0 = min(p[1] for p in pts) - 48.0
    y1 = max(p[1] for p in pts) + 48.0
    if x1 - x0 < 320.0:
        d = (320.0 - (x1 - x0)) / 2.0
        x0, x1 = x0 - d, x1 + d
    if y1 - y0 < 220.0:
        d = (220.0 - (y1 - y0)) / 2.0
        y0, y1 = y0 - d, y1 + d
    sx, sy, sw, sh = screen
    x0, y0 = math.floor(max(x0, sx)), math.floor(max(y0, sy))
    x1, y1 = math.ceil(min(x1, sx + sw)), math.ceil(min(y1, sy + sh))
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def marks(loops):
    """At most 48 points per stroke, whole points: plenty to tell a circle from a line."""
    out = []
    for s in loops:
        step = max(len(s) // 48, 1)
        picked = s[::step]
        if s and picked and s[-1] != picked[-1]:
            picked = picked + [s[-1]]
        out.append([[round(p[0]), round(p[1])] for p in picked])
    return out
