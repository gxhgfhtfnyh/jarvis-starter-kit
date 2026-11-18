"""Operation log processing utilities."""

def collect_entries(data, since_iso):
    """
    Collect entries from operation data that are strictly after since_iso.

    Args:
        data: Dict with "ops" key containing list of operation dicts.
        since_iso: ISO timestamp string to filter entries after.

    Returns:
        List of dicts with keys "ts", "title", "action", "kind", "text",
        sorted by ts ascending (stable).
    """
    result = []

    ops = data.get("ops", [])
    for op in ops:
        title = op.get("title", "")
        action = op.get("action", "")
        entries = op.get("entries", [])

        if not isinstance(entries, list):
            continue

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts")
            if not isinstance(ts, str):
                continue
            if ts > since_iso:
                kind = entry.get("kind", "")
                text = entry.get("text", "")
                result.append({
                    "ts": ts,
                    "title": title,
                    "action": action,
                    "kind": kind,
                    "text": text
                })

    # Stable sort by ts ascending
    result.sort(key=lambda x: x["ts"])
    return result


COMPACT_AT = 3


def format_section(entries, now_label):
    """
    Format entries as a markdown section.

    Args:
        entries: List of entry dicts with "ts", "title", "text" keys.
        now_label: Label for the section header.

    Returns:
        Markdown string, or empty string if entries is empty.
    """
    if not entries:
        return ""

    lines = [f"## Auto-log from operation notes ({now_label})"]

    # 2026-10-29: the daily note reached 65 KB (typical days 3 to 15 KB) because every step of every operation was mirrored here although the full
    # text already lives in the operation file. An operation with COMPACT_AT or more entries in one section becomes ONE summary line.
    by_title = {}
    for entry in entries:
        by_title.setdefault(entry.get("title", ""), []).append(entry)
    done = set()

    for entry in entries:
        title = entry.get("title", "")
        group = by_title[title]
        if len(group) >= COMPACT_AT:
            if title in done:
                continue
            done.add(title)
            first, last = group[0].get("ts", "")[11:16], group[-1].get("ts", "")[11:16]
            latest = group[-1].get("text", "")
            if len(latest) > 200:
                latest = latest[:200] + "..."
            lines.append(f"- {first}-{last} [{title}] {len(group)} steps (full text in the operation file); latest: {latest}")
            continue
        ts = entry.get("ts", "")
        # Extract HH:MM from timestamp (characters 11 to 16)
        hhmm = ts[11:16] if len(ts) >= 16 else ""
        text = entry.get("text", "")

        if len(text) > 300:
            text = text[:300] + "..."

        lines.append(f"- {hhmm} [{title}] {text}")

    return "\n".join(lines)