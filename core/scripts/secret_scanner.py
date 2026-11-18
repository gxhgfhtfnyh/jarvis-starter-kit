# -*- coding: utf-8 -*-
"""
Scans the vault for accidentally-plaintexted secrets - enforces the standing
"no secrets in handoff docs" rule with an actual check instead of just
intent. Never stores or logs the matched secret value itself, only where it
was found and what kind of pattern matched.

Usage:
    python secret_scanner.py

    # Item 62 (2026-08-09): scan the scripts folder instead of the vault
    from secret_scanner import scan
    from pathlib import Path
    scan(root=Path("scripts"), glob_pattern="*.py")
"""
import re
import sys
from pathlib import Path

VAULT_DIR = Path(r"J:\Jarvis-Brain")
SKIP_DIRS = {".obsidian", ".git", ".venv", "node_modules", "__pycache__"}

PATTERNS = {
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "Generic API key assignment": re.compile(r"(?i)\b(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{20,}['\"]?"),
    "OpenAI-style secret key": re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "Slack token": re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
    "Private key block": re.compile(r"-----BEGIN (RSA |EC |OPENSSH |)PRIVATE KEY-----"),
    "Generic password assignment": re.compile(r"(?i)\bpassword\s*[:=]\s*['\"]?\S{6,}['\"]?"),
    "Bearer token": re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}"),
    "Google OAuth client secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_\-]{20,}\b"),
}

# A handful of words that legitimately follow "password:"/"api_key:" in normal
# documentation prose without being a real leaked secret - keeps the scanner
# from crying wolf on sentences *about* credentials rather than containing one.
# Security review 2026-11-18: this must only ever suppress the GENERIC, low-confidence
# patterns (a bare "password = ..." or "api_key = ..." could really be prose about
# credentials) -- it must never suppress a specific, high-confidence token FORMAT
# (an AWS key, a GitHub/Slack/OpenAI/Google token, a private key block), since those
# are unambiguous regardless of what else is on the same line. Matched as whole words
# against the full line, not a substring (a real key sitting next to an unrelated word
# like "reset" must not be hidden just because "set" is a substring of "reset").
GENERIC_PATTERNS = {"Generic API key assignment", "Generic password assignment", "Bearer token"}
SAFE_VALUE_WORDS = {"never", "none", "n/a", "not", "keyring", "stored", "set"}
SAFE_VALUE_RX = re.compile(r"\b(?:%s)\b" % "|".join(re.escape(w) for w in SAFE_VALUE_WORDS), re.IGNORECASE)


def scan(root=None, glob_pattern="*.md"):
    # Item 62 of the 2026-08-09 tools brainstorm: root/glob_pattern let this
    # same engine scan the scripts folder for hardcoded secrets too, not
    # just vault markdown - a hardcoded key in code is just as real a leak.
    # Defaults preserve the original vault-only behavior exactly.
    root = root or VAULT_DIR
    findings = []
    for path in root.rglob(glob_pattern):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in ("token.json", "client_secret.json"):
            continue  # known legitimate credential files, not a leak to flag
        text = path.read_text(encoding="utf-8", errors="replace")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                matched_text = match.group(0)
                # Real bug found 2026-08-12: this only checked matched_text
                # itself, but a safe-context word like "keyring" almost always
                # sits elsewhere on the same line (e.g. "password = ... get
                # ... keyring ... archive_password") rather than inside the
                # narrow regex match - widen the check to the full line.
                line_start = text.rfind("\n", 0, match.start()) + 1
                line_end = text.find("\n", match.end())
                if line_end == -1:
                    line_end = len(text)
                full_line = text[line_start:line_end]
                if label in GENERIC_PATTERNS and SAFE_VALUE_RX.search(full_line):
                    continue
                # A comment *explaining* the pattern (documentation/examples)
                # isn't a real secret - same class of false positive as
                # doc-example wikilinks elsewhere in this vault's tooling.
                if full_line.strip().startswith("#"):
                    continue
                # Real false-positive class found live testing item 62
                # (2026-08-09) against actual code: "if not password:" is
                # Python control flow, not an assignment, but the generic
                # password/api-key pattern still matches its colon.
                preceding = text[max(0, match.start() - 20):match.start()]
                if re.search(r"\b(if|elif|while|assert)\b(?:\s+\S*)*\s*$", preceding, re.IGNORECASE):
                    continue
                line_no = text[:match.start()].count("\n") + 1
                findings.append({
                    "file": str(path.relative_to(root)),
                    "line": line_no,
                    "pattern": label,
                })
    return findings


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    findings = scan()
    if not findings:
        print("No potential secrets found in the vault.")
    else:
        print(f"=== {len(findings)} potential secret(s) found - VALUE NOT PRINTED ===\n")
        for f in findings:
            print(f"{f['file']}:{f['line']} - {f['pattern']}")
