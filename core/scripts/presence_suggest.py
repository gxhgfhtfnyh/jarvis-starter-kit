# -*- coding: utf-8 -*-
"""Superseded 2026-10-22: the owner switched the detector from SUGGEST to AUTO-SWITCH. This file is now a thin wrapper
around presence_auto.py (same CLI: --dry-run)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import presence_auto  # noqa: E402

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(presence_auto.main(sys.argv[1:]))
