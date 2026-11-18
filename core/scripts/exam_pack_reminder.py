# -*- coding: utf-8 -*-
"""One-shot exam-pack reminder for A364, 17 Aug 2026 -- fired by a Windows
Scheduled Task at noon, not dependent on any Claude session still being
alive (same reasoning as the daemon-persistence lesson elsewhere in this
codebase: anything that must fire unattended goes through schtasks + a
real script, not a session-scoped cron job)."""
import subprocess

subprocess.run([
    "python", r"J:\Jarvis2\scripts\discord_queue_send.py", "--stdin"
], input="dont forget to pack your exam stuff -- calculator, student card, the A4 double-sided notes sheet for A364, pens. exam's at 1pm, W54A, seat 15", text=True)
