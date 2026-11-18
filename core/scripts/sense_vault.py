# -*- coding: utf-8 -*-
"""Vault sensor: turns his rule 12 ("the vault is memory; keep it error-free unasked") from a prose rule into a running check (2026-10-30, Harness
Rebuild Instructions part 2, section 7 house style). Until now a broken link or an unlisted note was only found when someone happened to run vault_check.py
or vault_health_check.py; this notices it by itself and raises one intent per kind of problem, so the wake brief and the stop hook surface it.

Three read-only checks (it never writes inside the vault, and it never changes a note):
  (a) a [[wiki link]] that points at no existing note, only in notes edited in the last 3 days (older notes were cleaned to zero on 2026-10-29 by
      vault_check.py; this watches what has been written since). Resolution is Obsidian's: name or path suffix, case-insensitive, with or without .md,
      attachments by their full file name. Skipped on purpose: links in inline code or fenced blocks, in HTML comments, to a heading of the same note,
      template placeholders, and template notes themselves.
  (b) a note that sits in a folder with a folder index (frontmatter type: index) but is not linked from that index. Notes whose name starts with an
      underscore are exempt (05 - Resources/_canary.md is a tripwire that must never be referenced).
  (c) a verified fact whose probe in facts.json now fails, using the existing read-only runner facts_probe.probe (not its main, which logs).
Findings are grouped: ONE intent per kind with the count and the first 3 examples in `what` (tier 1; value 2 for a and b, 4 for c). The dedupe key is the
kind plus a hash of the sorted offenders, so an unchanged problem is raised once; when the problem changes, the older still-open intent of that kind is
closed as superseded so the queue never holds two versions of the same complaint. `what` never contains a double bracket, because a logger once copied
a quoted link example into a daily note and created a new broken link.
    python sense_vault.py [--dry-run]
--dry-run prints "would raise ..." and writes nothing, and still runs when the kill switch exists. Otherwise the kill switch logs/sense_vault_off.txt
makes it exit quietly.
Pure core (unit tested): wiki_targets(text), broken_links(notes, existing_titles), missing_from_index(folder_notes, index_text), index_gaps(folders),
failing_probes(probe_results), build_findings(broken, gaps, failing), superseded(intents, kind, dedupe), run_probes(facts, probe_fn)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import vault_check  # noqa: E402  (its EXTERNAL_FILES is the one list of non-note files the vault may link to)

VAULT = r"J:\Jarvis-Brain"
LOGS = r"J:\Jarvis2\logs"
FACTS = os.path.join(HERE, "facts.json")
OFF = "sense_vault_off.txt"
RECENT_DAYS = 3
PROBE_BUDGET_S = 45          # the sensor runner kills a sensor at 90 s; leave room for the walk and one slow probe
SHOW = 3                     # examples named in an intent
LINK = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
SPAN = re.compile(r"(?<!`)(`+)(?!`)([^\n]+?)(?<!`)\1(?!`)")     # a code span of N backticks closes at the next run of exactly N
FENCE = re.compile(r"^[ \t>]*(`{3,}|~{3,})(.*)$")
COMMENT = re.compile(r"<!--.*?-->", re.S)
PLACEHOLDER = ("{{", "}}", "<%", "%>", "${")


def _fence_marker(line):
    m = FENCE.match(line)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):    # ```inline``` is a code span, not a fence
        return None, ""
    return m.group(1), m.group(2)


def _strip_fences(text):
    """Text without fenced code blocks (``` or ~~~, closed by the same character at least as long; an unclosed fence runs to the end)."""
    out, fence = [], None
    for line in text.splitlines():
        marker, rest = _fence_marker(line)
        if fence is None:
            if marker:
                fence = marker
            else:
                out.append(line)
        elif marker and marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
            fence = None
    return "\n".join(out)


def wiki_targets(text):
    """Pure. The note (or file) names a text links to, in order: aliases, headings and block refs cut off, an escaped pipe from a table cell cleaned,
    embeds included. Links in code, in HTML comments, to a heading of the same note and template placeholders are not links."""
    text = COMMENT.sub("", _strip_fences(text or ""))
    out = []
    for line in text.splitlines():
        spans = [m.span() for m in SPAN.finditer(line)]
        for m in LINK.finditer(line):
            if any(a <= m.start() < b for a, b in spans):       # inside a code span: an example (a link whose own title contains backticks starts before the span)
                continue
            raw = m.group(1)
            if any(p in raw for p in PLACEHOLDER):
                continue
            target = raw.split("|")[0].split("#")[0].rstrip("\\").strip()
            if target:
                out.append(target)
    return out


def _is_template(rel):
    parts = rel.replace("\\", "/").lower().split("/")
    stem = parts[-1][:-3] if parts[-1].endswith(".md") else parts[-1]
    return stem.endswith("template") or "templates" in parts[:-1]


def _resolves(target, titles):
    t = target.replace("\\", "/").lower().strip()
    if t.endswith(".md"):
        t = t[:-3]
    return t in titles


def broken_links(notes, existing_titles):
    """Pure. notes: [(relative path, text)]; existing_titles: lowercase names a link may resolve to (see scan_vault). -> sorted unique [(path, target)]."""
    seen = {}
    for rel, text in notes or []:
        if not isinstance(text, str) or _is_template(rel):
            continue
        for t in wiki_targets(text):
            if not _resolves(t, existing_titles):
                seen.setdefault((rel, t.lower()), t)
    return sorted((rel, t) for (rel, _k), t in seen.items())


def is_index(text):
    """Pure. True when the note's frontmatter says type: index."""
    m = re.match(r"---\r?\n(.*?)\r?\n---", text or "", re.S)
    return bool(m and re.search(r"^type:\s*index\s*$", m.group(1), re.M))


def _listed(index_text):
    names = set()
    for t in wiki_targets(index_text):
        t = t.replace("\\", "/").rsplit("/", 1)[-1].lower()
        names.add(t[:-3] if t.endswith(".md") else t)
    body = _strip_fences(index_text or "")
    for m in re.finditer(r"\]\(([^)\s]+?)\.md(?:#[^)]*)?\)", body):      # [text](Note.md), with %20 for spaces
        names.add(m.group(1).replace("%20", " ").replace("\\", "/").rsplit("/", 1)[-1].lower())
    for line in body.splitlines():                                         # an entry written as the exact file name in code, `Note.md` - blurb: listed, just not clickable
        for m in SPAN.finditer(line):
            c = m.group(2).strip().replace("\\", "/")
            if c.lower().endswith(".md"):
                names.add(c.rsplit("/", 1)[-1][:-3].lower())
    return names


def missing_from_index(folder_notes, index_text):
    """Pure. -> sorted stems from folder_notes that the index does not link to. Names starting with an underscore are exempt (the canary tripwire)."""
    listed = _listed(index_text)
    return sorted(s for s in folder_notes or [] if not s.startswith("_") and s.lower() not in listed)


def index_gaps(folders):
    """Pure. folders: [{folder, index, notes, index_text}] -> sorted [(folder, index note, unlisted stem)]."""
    return sorted((f["folder"], f["index"], stem) for f in folders or [] for stem in missing_from_index(f["notes"], f["index_text"]))


def failing_probes(probe_results):
    """Pure. probe_results: [(fact, ok, detail)] -> [(fact, detail)] for the probes that now fail."""
    return [(fact, detail) for fact, ok, detail in probe_results or [] if not ok]


def run_probes(facts, probe_fn, budget_s=PROBE_BUDGET_S, clock=time.time):
    """-> ([(fact, ok, detail)], skipped). A probe that raises counts as failed (as facts_probe.main does), except a timeout, which says nothing about the
    fact and is skipped like every probe left over once the time budget is spent."""
    start, results, skipped = clock(), [], 0
    for item in facts or []:
        if clock() - start > budget_s:
            skipped += 1
            continue
        try:
            ok, detail = probe_fn(item)
        except subprocess.TimeoutExpired:
            skipped += 1
            continue
        except Exception as e:
            ok, detail = False, type(e).__name__
        results.append((item.get("fact", "?"), bool(ok), str(detail)))
    return results, skipped


def _plain(s, n):
    """One example as plain text: no double brackets (see the module note), cut to n characters."""
    return str(s).replace("[[", "(").replace("]]", ")").replace("\n", " ")[:n]


def _digest(items):
    return hashlib.sha1("\n".join(sorted(items)).encode("utf-8")).hexdigest()[:10]


def _group(kind, slug, value, head, offenders, examples):
    more = len(offenders) - SHOW
    what = "%s %s%s" % (len(offenders), head, "; ".join(examples[:SHOW]) + ("; +%d more" % more if more > 0 else ""))
    return (kind, what, value, "%s-%d-%s" % (slug, len(offenders), _digest(offenders)))


# Notes that state what is true NOW and so must not go quiet for long: path inside the vault -> longest age in days before it counts as stale (harness redo gap 8,
# staleness by age; the probes catch a fact that is wrong, this catches a note nobody has looked at). logs/living_notes.json can add or change entries.
LIVING_NOTES = {"Active Priorities.md": 3, "05 - Resources/Current Facts (verified).md": 10, "05 - Resources/Toolbox.md": 4, "05 - Resources/Security Posture.md": 30,
                "05 - Resources/Autonomy Tiers.md": 30, "VAULT-INDEX.md": 45}


def stale_living(ages, rules=None):
    """Pure. ages: {path: age in days or None if missing}. -> [(path, age days, limit days)] for living notes older than their limit, oldest first."""
    rules = LIVING_NOTES if rules is None else rules
    out = [(p, ages.get(p), lim) for p, lim in rules.items() if ages.get(p) is not None and ages[p] > lim]
    return sorted(out, key=lambda r: -(r[1] - r[2]))


def _living_ages(now):
    rules = dict(LIVING_NOTES)
    try:
        rules.update(json.load(open(os.path.join(LOGS, "living_notes.json"), encoding="utf-8")))
    except (OSError, ValueError):
        pass
    ages = {}
    for rel in rules:
        try:
            ages[rel] = (now - os.path.getmtime(os.path.join(VAULT, *rel.split("/")))) / 86400
        except OSError:
            ages[rel] = None
    return ages, rules


SNAPSHOT_LIMIT_DAYS = 2


def snapshot_stale(age_days, uncommitted, limit=SNAPSHOT_LIMIT_DAYS):
    """Pure. True when the vault's local git history has gone quiet while changes pile up. 2026-10-31: the hook that commits the vault failed silently from 2026-10-17 (a stale
    index.lock, then a course file path too long for git) and 225 changes went unversioned for two weeks before anyone looked."""
    return age_days is not None and uncommitted is not None and age_days > limit and uncommitted > 0


def _snapshot_state():
    """-> (age in days of the newest commit, number of uncommitted files) or None when the vault has no git history or git is missing. Status only runs when the history is old."""
    if not os.path.isdir(os.path.join(VAULT, ".git")):
        return None
    try:
        r = subprocess.run(["git", "-C", VAULT, "log", "-1", "--format=%ct"], capture_output=True, text=True, timeout=30)
        age = (time.time() - int(r.stdout.strip())) / 86400
        if age <= SNAPSHOT_LIMIT_DAYS:
            return age, 0
        s = subprocess.run(["git", "-C", VAULT, "status", "--porcelain"], capture_output=True, text=True, timeout=120)
        return age, len([ln for ln in s.stdout.splitlines() if ln.strip()])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def build_findings(broken, gaps, failing, days=RECENT_DAYS, stale=None, snapshot=None):
    """Pure. -> [(kind, what, value, dedupe)], at most one per kind, however many offenders there are."""
    out = []
    if snapshot and snapshot_stale(*snapshot):
        out.append(("vault_snapshot_stale", "the vault's git snapshots stopped: the newest is %d days old and %d file(s) are uncommitted. Run git add -A and git commit by hand in the vault to see "
                    "why (a stale .git/index.lock or a path too long for git are the usual causes; core.longpaths true fixes the second)" % (snapshot[0], snapshot[1]), 4,
                    "vault-snapshot-stale-%d" % int(snapshot[0] // 7)))
    if stale:
        out.append(_group("vault_stale_living", "vault-stale-living", 2, "living note(s) not touched for longer than their limit. First %d: " % SHOW,
                          [p for p, _a, _l in stale], ["%s (%d days, limit %d)" % (_plain(p, 50), a, l) for p, a, l in stale]))
    if broken:
        out.append(_group("vault_broken_links", "vault-broken-links", 2, "broken wiki link(s) in notes edited in the last %d days. First %d: " % (days, SHOW),
                          ["%s -> %s" % (rel, t) for rel, t in broken],
                          ['"%s" in %s' % (_plain(t, 60), _plain(rel, 70)) for rel, t in broken]))
    if gaps:
        out.append(_group("vault_index_gaps", "vault-index-gaps", 2, "note(s) not listed in their folder index. First %d: " % SHOW,
                          ["%s/%s" % (folder, stem) for folder, _i, stem in gaps],
                          ["%s/%s (index %s)" % (_plain(folder, 40), _plain(stem, 60), _plain(index, 30)) for folder, index, stem in gaps]))
    if failing:
        out.append(_group("vault_facts_failing", "vault-facts-failing", 4, "verified fact(s) now fail their probe. First %d: " % SHOW,
                          [fact for fact, _d in failing],
                          ["%s (%s)" % (_plain(fact, 70), _plain(detail, 40)) for fact, detail in failing]))
    return out


def superseded(intents, kind, dedupe):
    """Pure. The still-open, unleased intents of this kind raised under an older dedupe key: the problem changed, so they are stale."""
    return [i for i in intents if i.get("kind") == kind and i.get("status") == "open" and i.get("dedupe") != dedupe and not i.get("lease")]


def _suffixes(rel):
    parts = rel.split("/")
    return ["/".join(parts[i:]).lower() for i in range(len(parts))]


def scan_vault(root, now, days=RECENT_DAYS):
    """Impure, read-only. -> (existing titles, [(rel path, text) of notes edited in the last `days`], folders that have an index note).
    A note name resolves as every path suffix without .md; any other file as every path suffix with its extension."""
    titles = {t.lower() for t in vault_check.EXTERNAL_FILES}
    recent, by_folder = [], {}
    cutoff = now - days * 86400
    for d, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        rel_dir = os.path.relpath(d, root).replace(os.sep, "/")
        rel_dir = "" if rel_dir == "." else rel_dir
        for f in files:
            rel = (rel_dir + "/" + f) if rel_dir else f
            path = os.path.join(d, f)
            if not f.lower().endswith(".md"):
                titles.update(_suffixes(rel))
                continue
            titles.update(_suffixes(rel[:-3]))
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    if os.path.getmtime(path) >= cutoff:
                        text = fh.read()
                        recent.append((rel, text))
                        head = text[:1500]
                    else:
                        head = fh.read(1500)
            except OSError:
                continue
            by_folder.setdefault(rel_dir, []).append((f[:-3], is_index(head), path))
    folders = []
    for rel_dir, notes in sorted(by_folder.items()):
        indexes = [n for n in notes if n[1]]
        if not indexes:
            continue
        base = os.path.basename(rel_dir)
        idx = next((n for n in indexes if n[0] == base), sorted(indexes)[0])
        try:
            text = open(idx[2], encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        folders.append({"folder": rel_dir or "(vault root)", "index": idx[0], "notes": sorted(n[0] for n in notes if n is not idx), "index_text": text})
    return titles, recent, folders


def _facts_results():
    """-> (probe results or None, a short note). Uses the existing read-only probe; None means (c) is skipped and why."""
    try:
        import facts_probe
        with open(FACTS, encoding="utf-8") as fh:
            facts = json.load(fh)
    except (ImportError, OSError, ValueError) as e:
        return None, "facts probes unavailable (%s)" % type(e).__name__
    results, skipped = run_probes(facts, facts_probe.probe)
    return results, "%d fact(s) probed%s" % (len(results), (", %d skipped" % skipped) if skipped else "")


def main(argv=None):
    argv = argv or []
    dry = "--dry-run" in argv
    if not dry and os.path.exists(os.path.join(LOGS, OFF)):
        print("sense_vault: off")
        return 0
    if not os.path.isdir(VAULT):
        print("sense_vault: vault not found at %s, nothing checked" % VAULT)
        return 2
    titles, recent, folders = scan_vault(VAULT, time.time())
    results, facts_note = _facts_results()
    ages, rules = _living_ages(time.time())
    found = build_findings(broken_links(recent, titles), index_gaps(folders), failing_probes(results), stale=stale_living(ages, rules), snapshot=_snapshot_state())
    if dry:
        for kind, what, value, _dedupe in found:
            print("would raise v%d %-20s %s" % (value, kind, what))
        print("checked %d recent note(s), %d folder index(es); facts: %s" % (len(recent), len(folders), facts_note))
        print("%d finding(s), nothing written" % len(found))
        return 0
    import intent_store
    intents = intent_store.read_jsonl(intent_store.INTENTS)
    changed = bool(intent_store.expire_leases(intents))
    added = 0
    for kind, what, value, dedupe in found:
        cand = intent_store.new_intent(kind, what, tier=1, value=value, dedupe=dedupe, from_event=kind)
        if intent_store.is_duplicate(intents, cand):
            continue
        for old in superseded(intents, kind, dedupe):
            intent_store.apply_outcome(old, "skipped", "superseded by a newer sense_vault finding")
        intent_store.append(intent_store.EVENTS, {"ts": intent_store.iso(), "source": "vault", "kind": kind, "data": {"what": what, "value": value}})
        intents.append(cand)
        added += 1
        changed = True
    if changed:
        intent_store.write_intents(intents)
    print("sense_vault: %d finding(s), %d new intent(s)" % (len(found), added))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
