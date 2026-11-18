# -*- coding: utf-8 -*-
"""One pass of the security-alert watcher with de-duplication (2026-10-29, automation miner: the old Monitor loop re-emitted the same
two harmless 'jailbreak' keyword hits about every 5 minutes, and every emitted line wakes a full model turn with all hook text).
Prints something only when the (digits-normalised) alert text CHANGES from the last thing it printed, or when alerts clear and later return.
    python security_watch_once.py        # used inside:  while true; do python security_watch_once.py; sleep 300; done
Same noise filter and injection-baseline logic as the previous shell loop. State: logs/security_watch_state.json.
Pure helpers (filter_lines, normalise, decide) are unit tested."""
import hashlib
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = r"J:\Jarvis2\logs\security_watch_state.json"
NOISE = re.compile(r"process baseline|scheduled tasks / services|leaked-secret scan|ACL \+ startup-key audit|network destination baseline")


def filter_lines(out, injection_clean):
    """Drop known-noise lines; drop the vault injection line when injection_known.py says everything flagged is reviewed (injection_clean True)."""
    keep = []
    for ln in out.splitlines():
        if not ln.strip() or NOISE.search(ln):
            continue
        if "vault injection scan" in ln and injection_clean:
            continue
        keep.append(ln)
    return "\n".join(keep)


def normalise(text):
    """Digits -> N so '2 of 460 checks' and '2 of 461 checks' count as the same alert."""
    return re.sub(r"\d+", "N", text).strip()


def decide(prev_hash, text):
    """Returns (should_print, new_hash). Empty text prints nothing but resets the hash so a returning alert is reported."""
    if not text.strip():
        return False, ""
    h = hashlib.sha256(normalise(text).encode("utf-8")).hexdigest()
    return h != prev_hash, h


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=280)
    return (r.stdout or "") + (r.stderr or ""), r.returncode


def main():
    out, _ = run([sys.executable, os.path.join(HERE, "security_alert_check.py")])
    extra = ""
    injection_clean = True
    if "vault injection scan" in out:
        inj, rc = run([sys.executable, os.path.join(HERE, "injection_known.py")])
        injection_clean = rc == 0
        if not injection_clean:
            extra = "\n" + inj.strip()
    text = filter_lines(out, injection_clean) + extra
    try:
        prev = json.load(open(STATE, encoding="utf-8")).get("hash", "")
    except Exception:
        prev = ""
    should, new = decide(prev, text)
    json.dump({"hash": new}, open(STATE, "w", encoding="utf-8"))
    if should:
        print(text.strip())


if __name__ == "__main__":
    main()
