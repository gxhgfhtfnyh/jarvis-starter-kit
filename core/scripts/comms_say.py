# -*- coding: utf-8 -*-
"""
comms_say.py is the old name of office_say.py (Loom's Office replaced the COMMS chat on 2026-10-15). It is the same script: this file
imports the same function so nothing that still calls `comms_say.py` breaks.

    python J:\\Jarvis2\\scripts\\comms_say.py "short message"
    python J:\\Jarvis2\\scripts\\comms_say.py --stdin   < text
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from office_say import CHAT_LOG, MAX_CHARS, main, say  # noqa: E402,F401  (re-exported for callers and tests)

if __name__ == "__main__":
    sys.exit(main(sys.argv))
