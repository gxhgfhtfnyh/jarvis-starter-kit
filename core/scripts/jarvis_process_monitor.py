# -*- coding: utf-8 -*-
"""
Item 56 of the 2026-08-09 tools brainstorm: a real CPU/RAM check on
Jarvis's own long-running processes (avatar, voice_server, rvc_worker,
hotkey_listener) - catches a real leak or runaway process, distinct from
task_dashboard.py's uptime-only view of the avatar specifically.

Usage:
    from jarvis_process_monitor import check_processes
    python jarvis_process_monitor.py
"""
import sys

import psutil

WATCHED_MARKERS = ["avatar.py", "voice_server.py", "rvc_worker.py", "hotkey_listener.py"]
RAM_WARN_MB = 1500


def check_processes():
    results = []
    for p in psutil.process_iter(["pid", "name", "cmdline", "memory_info"]):
        try:
            cmdline = " ".join(p.info.get("cmdline") or [])
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        for marker in WATCHED_MARKERS:
            if marker in cmdline:
                mem_mb = p.info["memory_info"].rss / (1024 ** 2) if p.info.get("memory_info") else 0
                results.append({
                    "marker": marker, "pid": p.info["pid"],
                    "cpu_percent": p.cpu_percent(interval=0.1),
                    "mem_mb": round(mem_mb, 1),
                    "high_mem": mem_mb > RAM_WARN_MB,
                })
                break
    return results


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for r in check_processes():
        flag = "HIGH MEM" if r["high_mem"] else "ok"
        print(f"[{flag}] {r['marker']} (pid {r['pid']}): {r['cpu_percent']}% CPU, {r['mem_mb']} MB")
