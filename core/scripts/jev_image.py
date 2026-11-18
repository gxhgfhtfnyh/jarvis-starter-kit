# -*- coding: utf-8 -*-
"""Ask Jev (TypeSafe, key from Credential Manager item Typesafe-API-Key) a fast question about an image (2026-10-30, experiment 9a7334fa829e).
    python jev_image.py box <image> "<what to find>" --approved "<what he approved>"      # where is it: box edges as fractions 0..1 of width/height
    python jev_image.py yesno <image> "<question>" --approved "..."                       # probability of yes (0..1)
    python jev_image.py floating <image> --approved "..."                                 # does anything hover with nothing under it? flags at 0.30 or more
Measured 2026-10-30: on 5 named objects in his shrine stills the box hit (IoU >= 0.5) 5 of 5 (IoU 0.86 to 0.95); on 24 objects in drawn scenes 17 of 24; "floating"
ranked every pasted-in floating lantern above every untouched still (0.10 to 0.14 vs 0.40 to 0.69), so the flag threshold is 0.30, not 0.5 (at 0.5 it was 5 of 8 right,
at 0.30 it was 8 of 8; tiny sample, pasted defects, not a real floating pagoda). About 0.3 to 0.5 s and ~1.3k input tokens per image. A flag means "look at it", never "broken".
The image leaves the PC (third party), so this needs his approval of those images: pass --approved with what he said yes to (10+ chars); without it nothing is sent.
Pure core (unit tested): fit(size), build_request(...), parse_box(resp), parse_noul(resp), iou(a, b), flagged(p)."""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
URL = "https://api.typesafe.ai/v1/systemone"
MIN_SIDE, MAX_SIDE, MIN_PIXELS, MAX_PIXELS = 280, 1568, 78400, 2458624
FLOAT_FLAG = 0.30
FLOAT_Q = ("Is any solid object in `photo` (not a light, the moon or a star) hanging in mid-air, with nothing holding it up and no ground or structure under it?")


def fit(size):
    """Pure. -> (w, h) scaled to Jev's limits (each side 280..1568, 78,400..2,458,624 pixels); the shape is kept."""
    w, h = size
    s = min(1.0, MAX_SIDE / max(w, h), (MAX_PIXELS / float(w * h)) ** 0.5)
    s = max(s, MIN_SIDE / min(w, h), (MIN_PIXELS / float(w * h)) ** 0.5)
    return max(1, int(round(w * s))), max(1, int(round(h * s)))


def build_request(jpeg_bytes, questions):
    return {"model": "jev-latest", "state": {"photo": "img-1"},
            "assets": {"img-1": {"type": "image", "format": "jpg", "data_base64": base64.b64encode(jpeg_bytes).decode()}}, "questions": questions}


def parse_box(resp, name="q"):
    a = resp["answers"][name]
    b = a["box"]
    return {"left": b["left"], "top": b["top"], "right": b["right"], "bottom": b["bottom"], "confidence": a.get("confidence")}


def parse_noul(resp, name="q"):
    return float(resp["answers"][name]["noul"])


def iou(a, b):
    """Pure. a, b = (left, top, right, bottom) in the same units."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua if ua > 0 else 0.0


def flagged(p, threshold=FLOAT_FLAG):
    return p is not None and p >= threshold


def _jpeg(path):
    from PIL import Image
    im = Image.open(path).convert("RGB")
    im = im.resize(fit(im.size), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def ask(path, questions, key=None, timeout=60):
    """-> (response dict, milliseconds). Raises RuntimeError on an HTTP error with the status in the message."""
    import jev_bench
    body = build_request(_jpeg(path), questions)
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), method="POST",
                                 headers={"Authorization": "Bearer " + (key or jev_bench._key()), "Content-Type": "application/json", "TypeSafe-Danger-Zone-Images": "true"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode()), (time.time() - t) * 1000
    except urllib.error.HTTPError as e:
        raise RuntimeError("jev http %d: %s" % (e.code, e.read().decode()[:200]))


def _opt(argv, flag):
    return argv[argv.index(flag) + 1] if flag in argv and argv.index(flag) + 1 < len(argv) else None


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    approved = _opt(argv, "--approved") or ""
    if len(argv) < 2 or argv[0] not in ("box", "yesno", "floating"):
        print(__doc__.split("Pure core")[0].strip())
        return 2
    if len(approved) < 10:
        print("not sent: the image would go to a third party (Jev). Re-run with --approved \"<what he said yes to, 10+ chars>\".")
        return 3
    cmd, path = argv[0], argv[1]
    if not os.path.exists(path):
        print("no such image:", path)
        return 2
    text = argv[2] if len(argv) > 2 and not argv[2].startswith("--") else ""
    if cmd == "box":
        q = {"type": "box", "instructions": text + " in `photo`"}
    elif cmd == "yesno":
        q = {"type": "noul", "instructions": text}
    else:
        q = {"type": "noul", "instructions": FLOAT_Q}
    if cmd != "floating" and not text:
        print("give the question after the image path")
        return 2
    try:
        resp, ms = ask(path, {"q": q})
    except RuntimeError as e:
        print(e)
        return 1
    if cmd == "box":
        print("box %s (%.0f ms)" % (json.dumps(parse_box(resp)), ms))
    else:
        p = parse_noul(resp)
        print("p(yes)=%.2f%s (%.0f ms)" % (p, "  FLAG: look at it" if cmd == "floating" and flagged(p) else "", ms))
    return 0


if __name__ == "__main__":
    sys.exit(main())
