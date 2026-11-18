# -*- coding: utf-8 -*-
"""Derive a routines model from presence history, message timestamps and the class timetable,
so other scripts can ask "is this a bad time to ping him" instead of guessing.
Real v1, honest about sample size: as of 2026-11-04 the presence log only covers ~13 days
(2026-10-22 to 2026-11-04, 309 hourly records) -- confidence is reported per cell, not assumed.

Usage: python routines_model.py [--write]   (prints the profile; --write also saves
    logs/routines_model.json and a human-readable vault note)
"""
import json
import collections
from pathlib import Path

PRESENCE_LOG = Path(r"J:\Jarvis2\logs\presence_history.jsonl")
DISCORD_LOG = Path(r"J:\Jarvis2\logs\discord_incoming.jsonl")
TIMETABLE = Path(r"J:\Jarvis2\logs\timetable.json")
OUT_JSON = Path(r"J:\Jarvis2\logs\routines_model.json")
OUT_NOTE = Path(r"J:\Jarvis-Brain\05 - Resources\the owner's Routines (derived).md")

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_CONFIDENT_SAMPLES = 2  # below this, call it "not enough data" rather than guess


def load_jsonl(path):
    """Pure-ish: read a jsonl file, skip blank/corrupt lines rather than crash on one bad line."""
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def build_presence_profile(records):
    """Pure. (weekday, hour) -> {mode: count, total, dominant_mode, confidence}.
    confidence is just 'samples seen for this cell', the caller decides what's enough."""
    cells = collections.defaultdict(collections.Counter)
    for r in records:
        wd, hr, mode = r.get("weekday"), r.get("hour"), r.get("mode")
        if wd is None or hr is None or not mode:
            continue
        cells[(wd, hr)][mode] += 1
    profile = {}
    for key, counts in cells.items():
        total = sum(counts.values())
        dominant, dom_count = counts.most_common(1)[0]
        profile[key] = {
            "mode_counts": dict(counts),
            "total": total,
            "dominant_mode": dominant,
            "confidence": round(dom_count / total, 2),
        }
    return profile


def typical_mode(profile, weekday, hour):
    """What does the data say he's usually doing at this weekday/hour? Returns
    (mode_or_None, confidence, samples) -- None mode means not enough data, not a guess."""
    cell = profile.get((weekday, hour))
    if not cell or cell["total"] < MIN_CONFIDENT_SAMPLES:
        return None, 0.0, cell["total"] if cell else 0
    return cell["dominant_mode"], cell["confidence"], cell["total"]


def build_message_time_profile(records):
    """Pure. Hour (Singapore local, from 'date'/'ts' already being local per presence log
    convention) -> count of incoming messages from him specifically (author matches his known id),
    so we learn when he actually texts, not just when any message arrives."""
    import datetime as dt
    hour_counts = collections.Counter()
    for r in records:
        if r.get("source") != "dm":
            continue
        ts = r.get("date")
        if not ts:
            continue
        try:
            h = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).hour
        except (ValueError, TypeError):
            continue
        hour_counts[h] += 1
    return dict(hour_counts)


def load_timetable_blocks():
    """Real class blocks from the existing timetable source, not re-derived -- this model
    adds presence/message patterns on top of a timetable that's already maintained elsewhere."""
    if not TIMETABLE.exists():
        return []
    try:
        data = json.loads(TIMETABLE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    events = data.get("events", data) if isinstance(data, dict) else data
    return [e for e in events if not e.get("unclear")]


def summarize(profile, msg_hours, sample_start, sample_end, n_records):
    """Build the human-readable lines, honest about confidence throughout."""
    lines = [
        f"Derived from {n_records} presence snapshots, {sample_start} to {sample_end} "
        f"-- a real but short sample (under 3 weeks), confidence is per-cell, not assumed.",
        "",
    ]
    for wd in range(7):
        day_cells = [(h, profile.get((wd, h))) for h in range(24)]
        known = [(h, c) for h, c in day_cells if c and c["total"] >= MIN_CONFIDENT_SAMPLES]
        if not known:
            lines.append(f"**{WEEKDAY_NAMES[wd]}**: not enough data yet")
            continue
        parts = [f"{h:02d}:00 {c['dominant_mode']} ({int(c['confidence']*100)}%, n={c['total']})" for h, c in known]
        lines.append(f"**{WEEKDAY_NAMES[wd]}**: " + ", ".join(parts))
    if msg_hours:
        top = sorted(msg_hours.items(), key=lambda kv: -kv[1])[:5]
        lines.append("")
        lines.append("Message activity by hour (top 5): " + ", ".join(f"{h:02d}:00 ({c})" for h, c in top))
    return "\n".join(lines)


def main(write=False):
    presence = load_jsonl(PRESENCE_LOG)
    messages = load_jsonl(DISCORD_LOG)
    profile = build_presence_profile(presence)
    msg_hours = build_message_time_profile(messages)
    blocks = load_timetable_blocks()
    dates = [r["date"] for r in presence if r.get("date")]
    start, end = (min(dates), max(dates)) if dates else ("n/a", "n/a")
    summary = summarize(profile, msg_hours, start, end, len(presence))
    print(summary)
    print(f"\n{len(blocks)} known timetable block(s) loaded separately (not re-derived here).")
    if write:
        OUT_JSON.write_text(json.dumps({
            "profile": {f"{k[0]},{k[1]}": v for k, v in profile.items()},
            "message_hours": msg_hours,
            "sample_start": start,
            "sample_end": end,
            "n_presence_records": len(presence),
        }, indent=2), encoding="utf-8")
        note = (
            "---\nstatus: active\nproject: harness\ntype: reference\nlast_verified: 2026-11-04\n"
            "confidence: partial (short sample)\n---\n\n# the owner's Routines (derived)\n\n"
            "Built by `routines_model.py` from real presence/message history, not assumed. "
            "Re-run periodically as more data accumulates -- the sample is still short.\n\n"
            + summary
        )
        OUT_NOTE.write_text(note, encoding="utf-8")
        print(f"\nWrote {OUT_JSON} and {OUT_NOTE}")


if __name__ == "__main__":
    import sys
    main(write="--write" in sys.argv)
