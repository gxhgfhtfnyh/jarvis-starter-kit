# -*- coding: utf-8 -*-
"""Presence evidence gatherer (built 2026-10-22). READ-ONLY: no writes, no sends, no mode changes.

    python presence_signals.py          # prints the signals as JSON

Signals (all times are epoch seconds unless noted; None = unknown):
  idle_s            seconds since last keyboard/mouse input in THIS process's Windows session (GetLastInputInfo).
                    Only trusted when the session is the active one; the scheduled task JarvisPresenceSuggest runs
                    with the interactive token (pythonw, same as JarvisSchoolSessionCheck) so it IS in his session.
                    If the session is not Active (disconnected RDP etc.) the live reading is unreliable and we fall
                    back to the last-input record presence_suggest.py keeps (logs/presence_last_input.json),
                    flagged by idle_source = "record"; with no usable record idle_s is None.
  rdp_active        True if any RDP session (name starts "rdp-tcp#") is Active. Informational.
  game_on           from game_mode.py's state file (logs/game_mode.json; missing file = off, expired = off).
  last_discord_ts   his last Discord message (discord_incoming.jsonl, author_id = his id).
  last_telegram_ts  his last Telegram message. telegram_incoming.jsonl (user-account daemon) has NO from-self
                    marker, so it is not used; the bot log telegram_bot_incoming.jsonl has owner:true and is.
  last_ui_ts        his last message typed in the Archive Terminal (jarvis_ui_chat.jsonl, role "user").
  locked            input desktop unavailable (lock screen / disconnected). Monitor power is NOT readable without a window
                    receiving power broadcasts, so it is not used (it follows idle anyway).
  games             known game processes / anything running from steamapps/common, Epic, Riot folders.
  phone             confirmed phone MAC (presence_config.json phone_mac) in the ARP table / answering ping.
  prior             weekday+hour share of home/away/asleep over the last 14 days of presence_history.jsonl.
  discord status    NOT available: the daemon does not request the privileged presence intent; not used.
  class_now         the lesson he is "in class" for (now within start-15min .. end+15min, in-person only; async
                    e-learning is NOT class) from logs/timetable.json, plus class_end (end of the back-to-back chain).
"""
import ctypes
import json
import os
import sys
import time
from ctypes import wintypes
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import discord_queue_send as dqs  # noqa: E402

LOGS = Path(os.environ.get("HARNESS_LOGS") or Path(HERE).parent / "logs").resolve()
TZ = ZoneInfo(os.environ.get("JARVIS_TZ") or "Asia/Singapore")
THE_OWNER_DISCORD_ID = dqs.owner_id(LOGS)       # from logs/discord_config.json; None until the owner configures it
CLASS_MARGIN_MIN = 15

DISCORD_FILE = LOGS / "discord_incoming.jsonl"
TG_BOT_FILE = LOGS / "telegram_bot_incoming.jsonl"
UI_FILE = LOGS / "jarvis_ui_chat.jsonl"
TIMETABLE_FILE = LOGS / "timetable.json"
GAME_FILE = LOGS / "game_mode.json"
LAST_INPUT_FILE = LOGS / "presence_last_input.json"


# ---------------------------------------------------------------- idle / sessions
class _LII(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]


def raw_idle_seconds():
    """Seconds since last input in this process's session, or None if the call fails."""
    try:
        lii = _LII()
        lii.cbSize = ctypes.sizeof(_LII)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
            return None
        ctypes.windll.kernel32.GetTickCount.restype = ctypes.c_uint
        ms = (ctypes.windll.kernel32.GetTickCount() - lii.dwTime) & 0xFFFFFFFF
        return ms / 1000.0
    except Exception:
        return None


class _WTS_INFO(ctypes.Structure):
    _fields_ = [("SessionId", wintypes.DWORD), ("pWinStationName", wintypes.LPWSTR), ("State", wintypes.DWORD)]


_STATES = {0: "active", 1: "connected", 2: "connectquery", 3: "shadow", 4: "disconnected",
           5: "idle", 6: "listen", 7: "reset", 8: "down", 9: "init"}


def list_sessions():
    """[{id, name, state}] via WTSEnumerateSessionsW (no subprocess, so no console flash from pythonw)."""
    out = []
    try:
        w = ctypes.windll.wtsapi32
        ptr = ctypes.POINTER(_WTS_INFO)()
        count = wintypes.DWORD()
        w.WTSEnumerateSessionsW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                            ctypes.POINTER(ctypes.POINTER(_WTS_INFO)), ctypes.POINTER(wintypes.DWORD)]
        if not w.WTSEnumerateSessionsW(None, 0, 1, ctypes.byref(ptr), ctypes.byref(count)):
            return out
        for i in range(count.value):
            s = ptr[i]
            out.append({"id": int(s.SessionId), "name": s.pWinStationName or "", "state": _STATES.get(s.State, str(s.State))})
        w.WTSFreeMemory.argtypes = [ctypes.c_void_p]
        w.WTSFreeMemory(ptr)
    except Exception:
        pass
    return out


def own_session_id():
    try:
        sid = wintypes.DWORD()
        ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(sid))
        return int(sid.value)
    except Exception:
        return None


def session_kind(sid, sessions):
    """'console' | 'rdp' | 'other' | None for the given session id."""
    s = next((x for x in sessions if x["id"] == sid), None)
    if not s:
        return None
    n = s["name"].lower()
    return "console" if n == "console" else "rdp" if n.startswith("rdp-tcp#") else "other"


def read_last_input_record():
    try:
        return float(json.loads(LAST_INPUT_FILE.read_text(encoding="utf-8"))["last_input_ts"])
    except Exception:
        return None


def idle_signal(now_ts, sessions=None, own_sid=None, raw_idle=None, record_ts=None, use_live=True):
    """-> (idle_s, source, note). Pure given its inputs (defaults read the live system)."""
    sessions = list_sessions() if sessions is None else sessions
    own_sid = own_session_id() if own_sid is None else own_sid
    raw = (raw_idle_seconds() if use_live else None) if raw_idle is None else raw_idle
    record_ts = read_last_input_record() if record_ts is None else record_ts
    mine = next((s for s in sessions if s["id"] == own_sid), None)
    trusted = raw is not None and own_sid not in (None, 0) and mine is not None and mine["state"] == "active"
    if trusted:
        return raw, "live", f"session {own_sid} active"
    why = ("call failed" if raw is None else
           "running in session 0 (not interactive)" if own_sid in (None, 0) else
           f"session {own_sid} is {mine['state'] if mine else 'unknown'}, not active")
    if record_ts is not None:
        return max(0.0, now_ts - record_ts), "record", f"live idle unreliable ({why}); using last-input record"
    return None, "none", f"live idle unreliable ({why}); no last-input record"


# ---------------------------------------------------------------- message logs
def _tail_lines(path, max_bytes=400_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
        return data.decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _last_match(path, pred):
    for line in reversed(_tail_lines(path)):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if not isinstance(d, dict):
            continue
        ts = pred(d)
        if ts is not None:
            return ts
    return None


def last_discord_ts():
    def p(d):
        if d.get("author_id") == THE_OWNER_DISCORD_ID:
            try:
                return datetime.fromisoformat(d["date"]).timestamp()
            except Exception:
                return None
    return _last_match(DISCORD_FILE, p)


def last_telegram_ts():
    def p(d):
        if d.get("owner") is True:
            try:
                return datetime.fromisoformat(d["ts"]).replace(tzinfo=TZ).timestamp()
            except Exception:
                return None
    return _last_match(TG_BOT_FILE, p)


def last_ui_ts():
    def p(d):
        if d.get("role") == "user":
            try:
                return float(d["ts"])
            except Exception:
                return None
    return _last_match(UI_FILE, p)


# ---------------------------------------------------------------- game mode / class
def game_on(now_ts):
    try:
        d = json.loads(GAME_FILE.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(d, dict) or not d.get("on"):
        return False
    try:
        until = datetime.fromisoformat(d["until"])
        if until.tzinfo is None:
            until = until.replace(tzinfo=TZ)
        return until.timestamp() > now_ts
    except Exception:
        return True  # on with no/unreadable expiry: treat as on (the safe side for "do not disturb")


def class_now(now_dt, events=None):
    """-> {'title','start','end','class_end'} if now is within a lesson +- 15 min, else None."""
    if events is None:
        try:
            events = json.loads(TIMETABLE_FILE.read_text(encoding="utf-8"))["events"]
        except Exception:
            return None
    m = timedelta(minutes=CLASS_MARGIN_MIN)
    spans = []
    for e in events:
        mode = str(e.get("mode", "")).lower()
        if "async" in mode or "e-learning" in mode:
            continue
        try:
            s = datetime.fromisoformat(f"{e['date']}T{e['start']}").replace(tzinfo=TZ)
            en = datetime.fromisoformat(f"{e['date']}T{e['end']}").replace(tzinfo=TZ)
        except Exception:
            continue
        spans.append((s, en, e.get("title", "")))
    spans.sort()
    for s, en, title in spans:
        if s - m <= now_dt <= en + m:
            end = en
            for s2, en2, _ in spans:  # extend through back-to-back lessons (gap <= margin)
                if s2 <= end + m and en2 > end:
                    end = en2
            return {"title": title, "start": s.strftime("%H:%M"), "end": en.strftime("%H:%M"),
                    "class_end": end.strftime("%H:%M")}
    return None


# ---------------------------------------------------------------- lock / games / phone / prior
CONFIG_FILE = LOGS / "presence_config.json"
HISTORY_FILE = LOGS / "presence_history.jsonl"
KNOWN_GAMES = {"rainbowsix.exe", "rainbowsix_vulkan.exe", "valorant-win64-shipping.exe", "cs2.exe", "csgo.exe",
               "fortniteclient-win64-shipping.exe", "robloxplayerbeta.exe", "genshinimpact.exe", "dota2.exe",
               "eldenring.exe", "overwatch.exe", "r5apex.exe", "gta5.exe", "leagueoflegends.exe", "league of legends.exe",
               "minecraft.exe", "hades.exe", "hades2.exe", "stardewvalley.exe", "terraria.exe"}
GAME_DIR_HINTS = ("steamapps/common", "steamapps" + chr(92) + "common", "epic games", "riot games")
GAME_NOISE = ("crash", "overlay", "helper", "launcher", "setup", "redist", "webhelper", "install", "unins", "eac", "battleye",
              "riotclientservices", "riotclientux")  # 2026-11-06: always-on Riot background services, not the game itself


def is_locked():
    """True when the input desktop cannot be opened (lock screen / secure desktop / disconnected). None if unknown."""
    try:
        u = ctypes.windll.user32
        u.OpenInputDesktop.restype = ctypes.c_void_p
        h = u.OpenInputDesktop(0, False, 0x100)
        if not h:
            return True
        u.CloseDesktop(ctypes.c_void_p(h))
        return False
    except Exception:
        return None


def running_games():
    """Names of known game processes (list), reading the process table only."""
    try:
        import psutil
    except Exception:
        return []
    found = []
    for p in psutil.process_iter(["name", "exe"]):
        try:
            n = (p.info.get("name") or "").lower()
            exe = (p.info.get("exe") or "").lower()
        except Exception:
            continue
        if not n or any(x in n for x in GAME_NOISE):
            continue
        if n in KNOWN_GAMES or any(h in exe for h in GAME_DIR_HINTS):
            if n not in found:
                found.append(n)
    return found[:5]


def read_config():
    try:
        d = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def phone_signal():
    """{'configured': bool, 'ping': bool|None, 'in_arp': bool|None, 'ip': str|None}. Only for a CONFIRMED phone_mac."""
    mac = read_config().get("phone_mac")
    if not mac:
        return {"configured": False, "ping": None, "in_arp": None, "ip": None}
    try:
        import presence_devices as D
        r = D.phone_seen(mac, read_config().get("phone_ip"))
        return {"configured": True, "ping": r["ping"], "in_arp": r["in_arp"], "ip": r["ip"]}
    except Exception:
        return {"configured": True, "ping": None, "in_arp": None, "ip": None}


def prior(now_dt, path=None, days=14, min_samples=2):
    """Per weekday+hour share of home/away/asleep over the last `days` days from presence_history.jsonl, or None."""
    path = path or HISTORY_FILE
    cutoff = now_dt.timestamp() - days * 86400
    counts = {"home": 0, "away": 0, "asleep": 0}
    for line in _tail_lines(path, 2_000_000):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if (isinstance(d, dict) and d.get("ts", 0) >= cutoff and d.get("weekday") == now_dt.weekday()
                and d.get("hour") == now_dt.hour and d.get("mode") in counts):
            counts[d["mode"]] += 1
    n = sum(counts.values())
    if n < min_samples:
        return None
    return {**{k: v / n for k, v in counts.items()}, "n": n}


# ---------------------------------------------------------------- gather
def gather(now_ts=None):
    now_ts = time.time() if now_ts is None else now_ts
    now_dt = datetime.fromtimestamp(now_ts, TZ)
    sessions = list_sessions()
    idle, src, note = idle_signal(now_ts, sessions=sessions)
    return {
        "now": now_dt.isoformat(timespec="seconds"),
        "now_ts": now_ts,
        "idle_s": idle,
        "idle_source": src,
        "idle_note": note,
        "own_session": own_session_id(),
        "own_session_kind": session_kind(own_session_id(), sessions),
        "sessions": sessions,
        "rdp_active": any(s["name"].lower().startswith("rdp-tcp#") and s["state"] == "active" for s in sessions),
        "game_on": game_on(now_ts),
        "locked": is_locked(),
        "games": running_games(),
        "phone": phone_signal(),
        "prior": prior(now_dt),
        "last_discord_ts": last_discord_ts(),
        "last_telegram_ts": last_telegram_ts(),
        "last_ui_ts": last_ui_ts(),
        "class_now": class_now(now_dt),
    }


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(json.dumps(gather(), indent=2, ensure_ascii=False))
