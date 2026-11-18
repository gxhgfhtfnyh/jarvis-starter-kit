# -*- coding: utf-8 -*-
"""
All-lights on/off, v4 - v3's per-light finger-counting cancelled the same
day it was built (the owner: "lets cancel all that", wanted something even
simpler). No more individual light selection at all now.

Flow:
  - Circle your index finger -> "arms" the system (times out after
    ARM_TIMEOUT_SECONDS if nothing follows, so it can't get stuck waiting).
  - While armed, open palm -> turns ON every configured device, disarms.
  - While armed, closed fist -> turns OFF every configured device, disarms.

This drops v3's finger-count heuristic and per-device numbering entirely -
`gesture_devices.json` is now just a flat name list, no slots. Still uses
the same two-stage pipeline (PoseLandmarker finds you at range, crop+zoom,
GestureRecognizer on the zoomed patch) and the circle detector from v3,
both real pieces that carry over unchanged.

Two modes:
    python gesture_light_control.py --setup
        Console-only, no camera. Type device names one per line, blank
        line to stop. Overwrites gesture_devices.json.

    python gesture_light_control.py --tapo-ip <ip>
        Live mode as described above.

NOT YET LIVE-TESTED against a real camera/hands - the circle detector was
verified against a synthetic circular path (2026-08-14, v3), the two-stage
crop pipeline and RTSP/buffer handling were verified against the real Tapo
camera earlier the same session. The armed/timeout state machine itself is
new and untested.
"""
import argparse
import json
import math
import subprocess
import sys
import time
import urllib.parse
from collections import deque
from pathlib import Path

import cv2
import numpy as np
from mediapipe import Image, ImageFormat
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (
    GestureRecognizer, GestureRecognizerOptions,
    PoseLandmarker, PoseLandmarkerOptions, RunningMode,
)

from jarvis_keyring import get_password

MODEL_PATH = r"J:\Jarvis2\models\gesture_recognizer.task"
POSE_MODEL_PATH = r"J:\Jarvis2\models\pose_landmarker.task"
DEVICES_FILE = Path(r"J:\Jarvis2\logs\gesture_devices.json")
HAG_CONTROL = r"J:\Jarvis2\scripts\hag_control.ps1"

RIGHT_WRIST, LEFT_WRIST = 16, 15
CROP_PAD = 260          # padding (px) around the wrist(es) before upscaling
CROP_SCALE = 3
POSE_INPUT_WIDTH = 640
SMOOTHING = 0.5
MISS_GRACE_FRAMES = 5

GESTURE_STABLE_FRAMES = {"Open_Palm": 1, "Closed_Fist": 1}  # consecutive frames
                          # before each gesture edge-triggers - both now at 1
                          # (open palm dropped from 2) per the owner's live
                          # "more sensitive" request, 2026-08-15
ARM_TIMEOUT_SECONDS = 8    # how long "armed" (after a circle) waits for open/fist before giving up

CIRCLE_BUFFER_SECONDS = 1.5
CIRCLE_MIN_RADIUS = 0.04     # min bounding-box size (normalized) to count as a real circle, not jitter
CIRCLE_TRIGGER_DEGREES = 240  # cumulative rotation needed to call it a completed loop (was 300)


class CircleDetector:
    """Tracks one fingertip's recent path and flags a completed loop by
    summing the signed angle swept around the path's own centroid - a full
    circle sums to ~360 degrees regardless of size or starting point."""

    def __init__(self):
        self.points = deque()  # (timestamp, x, y)

    def update(self, x, y):
        now = time.time()
        self.points.append((now, x, y))
        while self.points and now - self.points[0][0] > CIRCLE_BUFFER_SECONDS:
            self.points.popleft()
        if len(self.points) < 8:
            return False

        xs = [p[1] for p in self.points]
        ys = [p[2] for p in self.points]
        cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
        radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
        if radius < CIRCLE_MIN_RADIUS:
            return False

        total = 0.0
        prev_angle = math.atan2(ys[0] - cy, xs[0] - cx)
        for _, x_i, y_i in list(self.points)[1:]:
            angle = math.atan2(y_i - cy, x_i - cx)
            delta = angle - prev_angle
            while delta > math.pi:
                delta -= 2 * math.pi
            while delta < -math.pi:
                delta += 2 * math.pi
            total += delta
            prev_angle = angle

        if abs(math.degrees(total)) >= CIRCLE_TRIGGER_DEGREES:
            self.points.clear()
            return True
        return False


class HandTracker:
    """Finds where you are at range (PoseLandmarker, robust past ~15ft) and
    crops+zooms onto your hand(s) before the fine gesture/finger-count
    detector runs on that patch. Crops around BOTH wrists when both are
    visible (needed for counting past 5 across two hands), falls back to
    whichever single wrist is visible otherwise. Smooths position and
    tolerates a few missed frames to avoid flicker."""

    def __init__(self, pose_landmarker):
        self.pose_landmarker = pose_landmarker
        self.box = None  # smoothed (x1, y1, x2, y2) in full-frame pixels
        self.misses = 0

    def locate(self, frame):
        h, w = frame.shape[:2]
        small = cv2.resize(frame, (POSE_INPUT_WIDTH, int(h * POSE_INPUT_WIDTH / w)))
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
        img = Image(image_format=ImageFormat.SRGB, data=rgb)
        result = self.pose_landmarker.detect(img)

        raw_box = None
        if result.pose_landmarks:
            lm = result.pose_landmarks[0]
            wrists = [lm[i] for i in (LEFT_WRIST, RIGHT_WRIST) if lm[i].visibility >= 0.3]
            if wrists:
                xs = [wr.x * w for wr in wrists]
                ys = [wr.y * h for wr in wrists]
                raw_box = (min(xs) - CROP_PAD, min(ys) - CROP_PAD,
                           max(xs) + CROP_PAD, max(ys) + CROP_PAD)

        if raw_box is not None:
            self.box = raw_box if self.box is None else tuple(
                SMOOTHING * b + (1 - SMOOTHING) * r for b, r in zip(self.box, raw_box)
            )
            self.misses = 0
        elif self.box is not None and self.misses < MISS_GRACE_FRAMES:
            self.misses += 1
        else:
            self.box = None

        if self.box is None:
            return None, None

        x1, y1, x2, y2 = self.box
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        crop = frame[y1:y2, x1:x2]
        if crop.size == 0:
            return None, None
        upscaled = cv2.resize(crop, None, fx=CROP_SCALE, fy=CROP_SCALE, interpolation=cv2.INTER_CUBIC)
        return upscaled, (x1, y1)


def load_devices():
    if DEVICES_FILE.exists():
        return json.loads(DEVICES_FILE.read_text(encoding="utf-8"))
    return []


def save_devices(devices):
    DEVICES_FILE.parent.mkdir(exist_ok=True)
    DEVICES_FILE.write_text(json.dumps(devices, indent=2), encoding="utf-8")


def run_setup():
    print("Type each HAG device name (room lights), one per line, blank line to stop.")
    devices = []
    while True:
        name = input("Device name (blank to stop): ").strip()
        if not name:
            break
        devices.append(name)
        save_devices(devices)  # write-through, same lesson as v2's calibrate bug
        print(f"  saved '{name}'")
    print(f"Done. {len(devices)} device(s) saved to {DEVICES_FILE}")


def toggle_device(name, state):
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-File", HAG_CONTROL,
         "-Device", name, "-State", state],
        capture_output=True, text=True,
    )


def make_recognizer():
    options = GestureRecognizerOptions(
        base_options=BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=RunningMode.IMAGE,
        num_hands=2,
        # lowered from the 0.5 default per the owner's live "more sensitive"
        # request, 2026-08-15 - trades some false-positive risk for catching
        # weaker/partial hand signals at range
        min_hand_detection_confidence=0.3,
        min_hand_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    return GestureRecognizer.create_from_options(options)


def make_pose_landmarker():
    options = PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=POSE_MODEL_PATH),
        running_mode=RunningMode.IMAGE,
        num_poses=1,
        min_pose_detection_confidence=0.3,
        min_pose_presence_confidence=0.3,
        min_tracking_confidence=0.3,
    )
    return PoseLandmarker.create_from_options(options)


def tapo_rtsp_url(keyring_service, ip, stream="stream1"):
    user = "Jarvis"
    pw = get_password(keyring_service, user)
    if not pw:
        raise RuntimeError(f"No stored credential for keyring service '{keyring_service}'")
    return f"rtsp://{urllib.parse.quote(user)}:{urllib.parse.quote(pw)}@{ip}:554/{stream}"


def open_source(source):
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video source: {source}")
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    return cap


def run_live(source, headless=False):
    cap = open_source(source)
    recognizer = make_recognizer()
    tracker = HandTracker(make_pose_landmarker())
    devices = load_devices()
    if not devices:
        print("No devices configured. Run with --setup first.")
        return
    print(f"Devices: {devices}")

    armed_since = None
    gesture_candidate, gesture_streak = None, 0
    last_committed_gesture = None
    circle = CircleDetector()

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        crop, offset = tracker.locate(frame)
        result = recognizer.recognize(Image(image_format=ImageFormat.SRGB,
                                             data=cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))) \
            if crop is not None else None

        gesture_name = None
        circled = False
        if result and result.hand_landmarks:
            index_tip = result.hand_landmarks[0][8]
            circled = circle.update(index_tip.x, index_tip.y)
        if result and result.gestures:
            # prefer an unambiguous open/fist read from any detected hand
            for hand_gestures in result.gestures:
                if hand_gestures and hand_gestures[0].category_name in ("Open_Palm", "Closed_Fist"):
                    gesture_name = hand_gestures[0].category_name
                    break

        if circled:
            armed_since = time.time()
            print("CIRCLE: armed, waiting for open palm (on) or fist (off)")
        elif armed_since and time.time() - armed_since > ARM_TIMEOUT_SECONDS:
            armed_since = None
            print("(armed window timed out, circle again)")

        # debounce + edge-trigger the commit gesture
        if gesture_name == gesture_candidate:
            gesture_streak += 1
        else:
            gesture_candidate, gesture_streak = gesture_name, 1
        needed = GESTURE_STABLE_FRAMES.get(gesture_candidate, 2)
        stable_gesture = gesture_candidate if gesture_streak >= needed else None

        if armed_since and stable_gesture != last_committed_gesture:
            if stable_gesture == "Open_Palm":
                print(f"ON: {devices}")
                for name in devices:
                    toggle_device(name, "on")
                armed_since = None
            elif stable_gesture == "Closed_Fist":
                print(f"OFF: {devices}")
                for name in devices:
                    toggle_device(name, "off")
                armed_since = None
        last_committed_gesture = stable_gesture

        if not headless:
            armed_txt = f"ARMED ({ARM_TIMEOUT_SECONDS - int(time.time() - armed_since)}s)" if armed_since else "-"
            hud = f"gesture: {gesture_name or '-'} | {armed_txt}"
            cv2.putText(frame, hud, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
            cv2.imshow("live - q to quit", frame)
            if (cv2.waitKey(1) & 0xFF) == ord("q"):
                break

    cap.release()
    if not headless:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=None, help="webcam index or raw RTSP URL")
    parser.add_argument("--tapo-ip", default=None, help="Tapo camera IP (credential pulled from keyring)")
    parser.add_argument("--keyring-service", default="tapo_camera_study",
                         help="keyring service name the camera credential was stored under")
    parser.add_argument("--setup", action="store_true")
    parser.add_argument("--headless", action="store_true", help="no preview window - for running unattended")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if args.setup:
        run_setup()
    else:
        if args.tapo_ip:
            source = tapo_rtsp_url(args.keyring_service, args.tapo_ip)
        elif args.source:
            source = args.source
        else:
            source = "0"
        run_live(source, headless=args.headless)
