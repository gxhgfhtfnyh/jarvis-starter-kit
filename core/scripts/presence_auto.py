# -*- coding: utf-8 -*-
"""Presence AUTO-SWITCHER with a scoring model (2026-10-22). Runs every 10 min (task JarvisPresenceSuggest).
Switches the mode by running set_presence_mode.py with PRESENCE_CHANGED_BY=auto, then sends ONE short Discord DM that
says WHY. Phrase-based switches ("I'm home", "going to sleep") are NOT here: Jarvis applies those himself.

    python presence_auto.py             # evaluate and (if due) switch + DM
    python presence_auto.py --dry-run   # print signals + score breakdown + decision; NO switch, NO DM, NO writes

Scoring model: every signal casts weighted, explainable votes for home / away / asleep. The full breakdown (all votes
and totals) is written to logs/presence_auto.log on every run.
  Explicit rules first:
    - game mode ON blocks everything.
    - console activity without RDP (physically at the PC) -> home, immediate (skips the 2-check rule).
  Votes (mode +weight):
    class + RDP active           away 5      class alone                away 2
    phone (confirmed MAC only):  on wifi (ping) home 3 | only in ARP home 1 | unseen >= 30 min away 3 | unseen less away 1
    PC locked (>= 10 min quiet)  away 1 (asleep 1 at night)
    typing at the PC (< 15 min)  home 1          just messaged (< 15 min)   home 1
    quiet >= 45 min, daytime     away 2          quiet >= 3 h, daytime      away 4
    quiet >= 45 min at night     asleep 5  (night = 00:00-06:30, or after 23:00 with last message >= 60 min ago)
    known game process running   home 2          weekday/hour prior (14 d)  up to 1.5 for a >= 60% share
  A mode wins only with score >= 4 AND a lead of >= 3 over every other mode (the current one included), for 2
  consecutive checks (streak older than 25 min is stale).
Guards: never switch to the current mode; at most one switch per 30 min from any source; override: if the last change
was NOT made by this script, auto-switching pauses: 45 min after a hand-made change (him, Jarvis, presence_apply undo),
4 h after a change made from his own words (presence_phrases.py, by=phrase); typing at the PC itself may still overrule a
hold once the change is 5 min old (logs/presence_last_change.json, written by set_presence_mode.py).
2026-10-30: messages from the phone no longer count as "he is home" or reset the daytime quiet clock (he chats from school);
the daytime "away" votes use the PC's input idle time only.
2026-10-31: while he IS messaging (< 15 min) the 3 h "quiet-long" vote drops from 4 to 2 (an idle PC alone flipped him to away at home).
Not used: Discord status/presence (the daemon does not request the privileged presence intent), monitor power state
(not readable without a window receiving power broadcasts), the camera (never).
Files: logs/presence_auto_state.json, presence_last_change.json, presence_auto.log (JSON lines), presence_history.jsonl
(one line per hour: mode + signals, feeds the prior), presence_config.json (phone_mac).
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import presence_signals as S  # noqa: E402
from set_presence_mode import get_mode  # noqa: E402

LOGS = S.LOGS
STATE_FILE = LOGS / "presence_auto_state.json"
CHANGE_FILE = LOGS / "presence_last_change.json"
LOG_FILE = LOGS / "presence_auto.log"

ACTIVE_S = 15 * 60
QUIET_S = 45 * 60
LONG_QUIET_S = 3 * 3600
LATE_MSG_QUIET_S = 60 * 60
FLIP_GAP_S = 30 * 60
OVERRIDE_S = 2 * 3600                 # how long presence_apply's "undo" works; no longer the auto pause
MANUAL_HOLD_S = 45 * 60               # auto pauses this long after a hand-made change (him, me): enough to stop a flip-flop, short enough to correct a stale mode
PHRASE_HOLD_S = 4 * 3600              # after he SAID where he is ("im at sch", "gn"), his word beats weak signals this long
HOLD_BYPASS_AGE_S = 5 * 60            # typing at the PC itself (the explicit rule) may overrule a hold once the last change is this old
STREAK_STALE_S = 25 * 60
NEED_CHECKS = 2
DAY_START = 6 * 60 + 30
LATE_START = 23 * 60
EARLY_NIGHT_END = 60                  # minutes after midnight: until 01:00 (and from LATE_START) silence alone is a weak sign of sleep
EARLY_QUIET_S = 2 * 3600              # 2026-10-30 23:25: switched him to asleep after 58 quiet minutes and he was awake ("im awake lol"); of his 15 "gn" statements in the log only 1 came before 00:25 (median about 02:50), so early evenings need two hours of silence
MARGIN = 3.0
MIN_TOP = 4.0
PHONE_GONE_S = 30 * 60


def _read_json(path, default):
    try:
        d = json.loads(Path(path).read_text(encoding="utf-8"))
        return d if isinstance(d, type(default)) else default
    except Exception:
        return default


def _write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _fmt_dur(s):
    m = int(s // 60)
    return f"{m // 60}h{m % 60:02d}m" if m >= 60 else f"{m}m"


def score(sig, mode, state, now_ts):
    """Explainable vote model. -> {"votes": [{signal, mode, w, phrase}], "totals": {mode: score}, "immediate": dict|None}.
    Every vote carries the phrase used in the DM; the explicit immediate rule is listed separately."""
    votes = []

    def vote(signal, m, w, phrase):
        votes.append({"signal": signal, "mode": m, "w": round(float(w), 2), "phrase": phrase})

    now_dt = datetime.fromtimestamp(now_ts, S.TZ)
    minute = now_dt.hour * 60 + now_dt.minute
    night = minute < DAY_START
    late = minute >= LATE_START
    day = not night and not late
    rdp = bool(sig.get("rdp_active"))
    cls = sig.get("class_now")
    idle = sig.get("idle_s")

    msg = {}
    for key, name in (("last_discord_ts", "discord"), ("last_telegram_ts", "telegram"), ("last_ui_ts", "terminal")):
        if sig.get(key):
            msg[name] = max(0.0, now_ts - sig[key])
    ages = dict(msg)
    if idle is not None:
        ages["input"] = float(idle)
    active_s = min(ages.values()) if ages else None
    last_msg_s = min(msg.values()) if msg else None

    immediate = None
    if (sig.get("own_session_kind") == "console" and sig.get("idle_source") == "live" and idle is not None
            and idle < ACTIVE_S and not rdp):
        immediate = {"mode": "home", "phrase": "you're active at the PC itself"}
        vote("console", "home", 100, immediate["phrase"])

    if cls and rdp:
        vote("class+rdp", "away", 5, f"class till {cls['class_end']} and you're on rdp")
    elif cls and idle is not None and idle >= ACTIVE_S:
        # 2026-11-05: class_now is already in-person-only (presence_signals.py excludes async); a scheduled
        # in-person class plus the PC sitting untouched for even the same ACTIVE_S bar used for "recently active"
        # elsewhere is strong combined evidence, no need to wait for the full 45min QUIET_S bar the way "class
        # alone" (he could still be skipping and on the PC) must
        vote("class+quiet", "away", 5, f"class till {cls['class_end']} and you've been off the pc {_fmt_dur(idle)}")
    elif cls:
        vote("class", "away", 2, f"class till {cls['class_end']}")

    ph = sig.get("phone") or {}
    if ph.get("configured"):
        if ph.get("ping"):
            vote("phone", "home", 3, "your phone is on the home wifi")
        elif ph.get("in_arp"):
            vote("phone", "home", 1, "your phone is still in the router's list")
        else:
            last_seen = state.get("phone_last_seen")
            gone = (now_ts - last_seen) if last_seen else None
            if gone is not None and gone >= PHONE_GONE_S:
                vote("phone", "away", 3, f"your phone left the wifi {_fmt_dur(gone)} ago")
            else:
                vote("phone", "away", 1, "your phone isn't answering on the wifi")

    if sig.get("locked") and active_s is not None and active_s >= 10 * 60:
        if night or late:
            vote("lock", "asleep", 1, "the PC is locked")
        else:
            vote("lock", "away", 1, "the PC is locked")

    if idle is not None and idle < ACTIVE_S:
        vote("input", "home", 1, "you're typing at the PC")
    if last_msg_s is not None and last_msg_s < ACTIVE_S and idle is None:
        vote("message", "home", 1, "you just messaged")        # a message from the phone says nothing about WHERE he is; it counts only when the PC gives no reading

    need_quiet = EARLY_QUIET_S if (late or minute < EARLY_NIGHT_END) else QUIET_S
    if active_s is not None and active_s >= need_quiet:
        quiet = _fmt_dur(active_s)
        if night or (late and last_msg_s is not None and last_msg_s >= LATE_MSG_QUIET_S):
            vote("quiet-night", "asleep", 5, f"no activity for {quiet} at night")
    pc_quiet = float(idle) if idle is not None else active_s            # by day only the PC counts: chatting on the phone from school used to reset "quiet" and read as "home"
    if day and pc_quiet is not None and pc_quiet >= QUIET_S:
        quiet = _fmt_dur(pc_quiet)
        # 2026-10-31 20:45: an idle PC alone switched him to away after 7h39m while he chatted on his phone at home ("this was wrong").
        # While he is messaging (< 15 min) the idle PC is weak evidence; it needs class, the phone leaving the wifi or the lock to reach the threshold.
        chatting = last_msg_s is not None and last_msg_s < ACTIVE_S
        if pc_quiet >= LONG_QUIET_S and not chatting:
            vote("quiet-long", "away", 4, f"you haven't been at the PC for {quiet}")
        else:
            vote("quiet", "away", 2, f"you haven't been at the PC for {quiet}")

    games = sig.get("games") or []
    if games:
        vote("games", "home", 2, f"a game is running ({games[0]})")

    pr = sig.get("prior")
    if pr:
        best = max(("home", "away", "asleep"), key=lambda m: pr.get(m, 0))
        if pr.get(best, 0) >= 0.6:
            vote("prior", best, 1.5 * pr[best], f"you're usually {best} at this time")

    totals = {"home": 0.0, "away": 0.0, "asleep": 0.0}
    for v in votes:
        if v["w"] < 100:
            totals[v["mode"]] += v["w"]
    return {"votes": votes, "totals": {k: round(v, 2) for k, v in totals.items()}, "immediate": immediate}


def why_text(votes, mode):
    mine = sorted((v for v in votes if v["mode"] == mode and v["signal"] != "prior"), key=lambda v: -v["w"])[:3]
    if not mine:
        mine = [v for v in votes if v["mode"] == mode][:1]
    ph = [v["phrase"] for v in mine]
    if len(ph) <= 1:
        return ph[0] if ph else "scores favour it"
    return ", ".join(ph[:-1]) + " and " + ph[-1]


def decide(sig, mode, state, change, now_ts=None):
    """Pure. -> (action or None, notes, new_streak, breakdown). action = {"mode","reason","text"}."""
    now_ts = sig.get("now_ts", time.time()) if now_ts is None else now_ts
    if sig.get("game_on"):
        return None, ["game mode is ON: no switching"], {}, None
    bd = score(sig, mode, state, now_ts)
    notes = ["totals: " + ", ".join(f"{k}={v}" for k, v in bd["totals"].items())]
    immediate = bd["immediate"]
    by = (change or {}).get("by")
    hold = 0 if by in (None, "auto") else (PHRASE_HOLD_S if by == "phrase" else MANUAL_HOLD_S)
    age = now_ts - float((change or {}).get("ts", 0))
    if change and hold and age < hold and not (immediate and age >= HOLD_BYPASS_AGE_S):
        kind = "his words" if by == "phrase" else "hand"
        notes.append(f"override: mode was set by {kind} {_fmt_dur(age)} ago, auto paused for another {_fmt_dur(hold - age)}")
        return None, notes, {}, bd
    if immediate:
        cand = immediate["mode"]
        notes.append(f"explicit rule: {immediate['phrase']} -> {cand} [immediate]")
    else:
        totals = bd["totals"]
        cand = max(totals, key=totals.get)
        others = max(v for k, v in totals.items() if k != cand)
        if totals[cand] < MIN_TOP or totals[cand] - others < MARGIN:
            notes.append(f"no clear leader (top {cand}={totals[cand]}, needs >= {MIN_TOP:g} and a lead of "
                         f">= {MARGIN:g} over the runner-up {others})")
            return None, notes, {}, bd
        notes.append(f"leader: {cand}={totals[cand]} leads by {round(totals[cand] - others, 2)}")
    if cand == mode:
        notes.append(f"nothing to do: already {cand}")
        return None, notes, {}, bd
    streak = state.get("streak") or {}
    if streak.get("mode") == cand and now_ts - float(streak.get("ts", 0)) <= STREAK_STALE_S:
        count = int(streak.get("count", 1)) + 1
    else:
        count = 1
    new_streak = {"mode": cand, "count": count, "ts": now_ts}
    if change and now_ts - float(change.get("ts", 0)) < FLIP_GAP_S:
        notes.append(f"held: last switch was {_fmt_dur(now_ts - float(change['ts']))} ago (< 30m)")
        return None, notes, new_streak, bd
    if not immediate and count < NEED_CHECKS:
        notes.append(f"waiting: check {count} of {NEED_CHECKS} agrees on {cand}")
        return None, notes, new_streak, bd
    reason = immediate["phrase"] if immediate else why_text(bd["votes"], cand)
    text = f"switched you to {cand} ({reason}). say undo to revert"
    return {"mode": cand, "reason": reason, "text": text}, notes, {}, bd


def _log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now(S.TZ).isoformat(timespec='seconds')} {msg}\n")
    except OSError:
        pass


def _python_exe():
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        cand = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(cand):
            return cand
    return exe


def switch_mode(mode):
    env = dict(os.environ, PRESENCE_CHANGED_BY="auto", PYTHONIOENCODING="utf-8")
    r = subprocess.run([_python_exe(), str(HERE / "set_presence_mode.py"), mode], capture_output=True, timeout=60,
                       env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0, (r.stdout + r.stderr).decode("utf-8", "replace").strip()


def send_dm(text):
    r = subprocess.run([_python_exe(), str(HERE / "discord_queue_send.py"), "--stdin", "--dm"],
                       input=text.encode("utf-8"), capture_output=True, timeout=60,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.returncode == 0 and b"QUEUED" in r.stdout, (r.stdout + r.stderr).decode("utf-8", "replace").strip()


def _append_history(sig, mode, now_ts):
    """One line per local hour (first run in that hour): mode + signal summary, for the weekday/hour prior."""
    dt = datetime.fromtimestamp(now_ts, S.TZ)
    key = (dt.date().isoformat(), dt.hour)
    try:
        for line in S._tail_lines(S.HISTORY_FILE, 20000)[-3:]:
            d = json.loads(line)
            if (d.get("date"), d.get("hour")) == key:
                return False
    except Exception:
        pass
    ph = sig.get("phone") or {}
    rec = {"ts": now_ts, "date": key[0], "weekday": dt.weekday(), "hour": dt.hour, "mode": mode,
           "idle_s": sig.get("idle_s"), "rdp": sig.get("rdp_active"), "kind": sig.get("own_session_kind"),
           "class": bool(sig.get("class_now")), "locked": sig.get("locked"), "games": sig.get("games"),
           "phone_seen": (bool(ph.get("ping") or ph.get("in_arp")) if ph.get("configured") else None)}
    with open(S.HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return True


def _maybe_find_phone(sig, mode, state):
    """First time he is physically home after an away snapshot exists: take the home snapshot (background) + diff."""
    try:
        import presence_find_phone as F
        if (state.get("find_phone_started") or S.read_config().get("phone_mac") or not F.AWAY.exists()
                or F.HOME.exists() or mode != "home" or sig.get("own_session_kind") != "console"
                or sig.get("rdp_active") or (sig.get("idle_s") or 1e9) >= ACTIVE_S):
            return False
        subprocess.Popen([_python_exe(), str(HERE / "presence_find_phone.py"), "snapshot", "home"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) | 0x00000008)
        state["find_phone_started"] = time.time()
        return True
    except Exception:
        return False


def run(dry_run=False, sig=None, mode=None, change=None):
    sig = S.gather() if sig is None else sig
    now_ts = sig["now_ts"]
    mode = get_mode() if mode is None else mode
    change = _read_json(CHANGE_FILE, {}) if change is None else change
    state = _read_json(STATE_FILE, {})
    ph = sig.get("phone") or {}
    if ph.get("configured") and (ph.get("ping") or ph.get("in_arp")):
        state["phone_last_seen"] = now_ts
    if sig.get("idle_source") == "live" and sig.get("idle_s") is not None and not dry_run:
        _write_json(S.LAST_INPUT_FILE, {"last_input_ts": now_ts - sig["idle_s"], "written": now_ts})
    action, notes, streak, bd = decide(sig, mode, state, change, now_ts)
    out = {"mode": mode, "signals": sig, "last_change": change or None, "notes": notes, "streak": streak,
           "breakdown": bd, "action": "none"}
    mode_after = mode
    if action:
        out["decision"] = action
        if dry_run:
            out["action"] = f"WOULD switch to {action['mode']} and DM: {action['text']}"
            return out
        ok, msg = switch_mode(action["mode"])
        if not ok:
            out["action"] = f"SWITCH FAILED: {msg}"
            _log(f"mode={mode} switch to {action['mode']} FAILED: {msg}")
            return out
        dm_ok, dm_msg = send_dm(action["text"])
        out["action"] = "switched" + ("" if dm_ok else f" (DM FAILED: {dm_msg})")
        mode_after = action["mode"]
    if not dry_run:
        _log(json.dumps({"mode": mode, "action": out["action"], "notes": notes,
                         "totals": (bd or {}).get("totals"), "votes": (bd or {}).get("votes"),
                         "dm": (action or {}).get("text")}, ensure_ascii=False))
        try:
            _append_history(sig, mode_after, now_ts)
        except Exception as e:
            _log(f"history append failed: {e}")
        _maybe_find_phone(sig, mode_after, state)
        state["streak"] = streak
        _write_json(STATE_FILE, state)
    return out


def main(argv):
    dry = "--dry-run" in argv
    out = run(dry_run=dry)
    if dry:
        s = dict(out["signals"])
        s.pop("sessions", None)
        out["signals"] = s
        print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    try:
        sys.exit(main(sys.argv[1:]))
    except Exception as e:
        _log(f"ERROR {type(e).__name__}: {e}")
        raise
