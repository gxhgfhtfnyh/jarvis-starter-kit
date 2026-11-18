# -*- coding: utf-8 -*-
"""Real security use for the room camera (2026-08-20, the owner's own
follow-up pick from the brainstorm): while presence mode is away/asleep,
periodically grab a frame and compare it against the last one. If
something genuinely changed (not just lighting flicker/camera noise),
save the frame and flag it -- a live Jarvis session then actually LOOKS
at the frame and describes what it is, same as any other image, rather
than a normal camera app's context-free "motion detected" spam.

This script only does the CHEAP part (frame diffing) -- no LLM/vision
call happens here, that's what makes running it on a schedule sane. The
actual "look and judge if this is fine" step happens in the live Jarvis
session that gets woken by the flag, same pattern as every other
security-alert check in this project.

Self-gates on presence mode: does nothing (silent exit) when mode is
"home", since that's pointless/annoying to run while the owner's actually
around and the camera constantly sees him move.

Usage:
    python room_security_check.py
"""
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from gesture_light_control import tapo_rtsp_url
from set_presence_mode import get_mode
from win_toast import show_toast

import cv2
import numpy as np

TAPO_IP = "192.168.10.131"
KEYRING_SERVICE = "tapo_camera_study"
BASELINE_PATH = Path(r"J:\Jarvis2\logs\room_snapshots\baseline.jpg")
FLAGGED_DIR = Path(r"J:\Jarvis2\logs\room_snapshots\flagged")
LOG_PATH = Path(r"J:\Jarvis2\logs\room_security_check.jsonl")
# fraction of pixels that must change (after blur+threshold) to count as
# real motion, not sensor noise/lighting flicker -- tuned conservatively
# high since a false "empty room, nothing changed" is fine, a false
# alarm every 5 min would just get ignored (the exact alert-fatigue
# problem already fixed once tonight in process_baseline.py)
MOTION_FRACTION_THRESHOLD = 0.03


class MissingCredential(Exception):
    """Raised when the Tapo keyring credential isn't stored yet -- a known, tier-2,
    waiting-on-him state, not a fresh camera failure."""


def _capture_frame():
    try:
        url = tapo_rtsp_url(KEYRING_SERVICE, TAPO_IP)
    except RuntimeError:
        # known standing gap (no stored camera credential yet, tracked as its own
        # tier-2 ask -- only he can provide the password): a crash every 5 min for
        # an already-flagged, already-waiting-on-him state is noise, not a new signal
        raise MissingCredential(KEYRING_SERVICE)
    cap = cv2.VideoCapture(url)
    if not cap.isOpened():
        return None
    frame = None
    for _ in range(5):
        ok, f = cap.read()
        if ok:
            frame = f
    cap.release()
    return frame


def _motion_fraction(frame_a, frame_b):
    gray_a = cv2.cvtColor(frame_a, cv2.COLOR_BGR2GRAY)
    gray_b = cv2.cvtColor(frame_b, cv2.COLOR_BGR2GRAY)
    gray_a = cv2.GaussianBlur(gray_a, (21, 21), 0)
    gray_b = cv2.GaussianBlur(gray_b, (21, 21), 0)
    diff = cv2.absdiff(gray_a, gray_b)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    changed = np.count_nonzero(thresh)
    return changed / thresh.size


def check():
    mode = get_mode()
    result = {"timestamp": dt.datetime.now().isoformat(timespec="seconds"), "mode": mode}
    if mode == "home":
        result["skipped"] = "home mode"
        _log(result)
        return result

    try:
        frame = _capture_frame()
    except MissingCredential:
        # a known, already-flagged, tier-2 waiting-on-him state (see intent capability_red
        # 'look through the study camera'/capability gap) -- not a fresh failure, so this
        # exits clean (0) instead of crashing the scheduled task every 5 minutes for
        # something only he can actually fix (the real Tapo account password)
        result["skipped"] = "no stored camera credential yet (known gap, waiting on him)"
        _log(result)
        return result
    if frame is None:
        result["error"] = "could not capture frame"
        _log(result)
        return result

    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not BASELINE_PATH.exists():
        cv2.imwrite(str(BASELINE_PATH), frame)
        result["first_run"] = True
        _log(result)
        return result

    baseline = cv2.imread(str(BASELINE_PATH))
    fraction = _motion_fraction(baseline, frame)
    result["motion_fraction"] = round(fraction, 4)
    result["flagged"] = fraction >= MOTION_FRACTION_THRESHOLD

    if result["flagged"]:
        FLAGGED_DIR.mkdir(parents=True, exist_ok=True)
        out_path = FLAGGED_DIR / f"{dt.datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
        cv2.imwrite(str(out_path), frame)
        result["flagged_path"] = str(out_path)
        show_toast("Jarvis - room camera flagged motion", f"mode={mode}, check the flagged frame")

    # always update baseline to the latest frame, flagged or not -- so
    # gradual real changes (someone left a bag on the bed) don't keep
    # re-triggering every single check afterward
    cv2.imwrite(str(BASELINE_PATH), frame)
    _log(result)
    return result


def _log(result):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    import json
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(result) + "\n")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    r = check()
    print(r)
