# -*- coding: utf-8 -*-
"""
Thin wrapper around `keyring` that logs every credential access (service +
username + timestamp only - NEVER the value) to J:\\Jarvis2\\logs\\keyring_access.jsonl,
for an audit trail of when each secret was actually pulled.

Drop-in replacement: use `from jarvis_keyring import get_password` instead of
`from keyring import get_password` / `import keyring; keyring.get_password(...)`.
"""
import datetime as dt
import json
from pathlib import Path

import keyring as _keyring

LOG_PATH = Path(r"J:\Jarvis2\logs\keyring_access.jsonl")


def get_password(service, username):
    value = _keyring.get_password(service, username)
    LOG_PATH.parent.mkdir(exist_ok=True)
    entry = {
        "timestamp": dt.datetime.now().isoformat(timespec="seconds"),
        "service": service,
        "username": username,
        "found": value is not None,
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    return value


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not LOG_PATH.exists():
        print("No keyring access logged yet.")
    else:
        for line in LOG_PATH.read_text(encoding="utf-8").splitlines()[-20:]:
            e = json.loads(line)
            print(f"{e['timestamp']}  {e['service']}/{e['username']}  found={e['found']}")
