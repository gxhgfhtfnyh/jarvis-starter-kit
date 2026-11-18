# -*- coding: utf-8 -*-
"""
Global hotkey listener - Ctrl+Alt+J brings the terminal to the foreground instantly,
without alt-tabbing to find it. Runs persistently in the background (register it to
autostart at logon if you want it always available - see the .bat/Task Scheduler
note at the bottom).

Uses the Win32 RegisterHotKey API (via ctypes), not the `keyboard` package. Real bug,
found 2026-08-10: `keyboard` installs a low-level global keyboard hook that runs a
Python callback on EVERY keystroke system-wide (not just the hotkey combo), so it
sits directly in the OS's per-keystroke input path. When this process got starved for
a moment - observed happening when Claude Code's avatar-mood hooks spawn a burst of
fresh powershell.exe/python.exe processes on rapid tool-call sequences, across two
concurrent sessions - Windows' hook-timeout handling let keystrokes typed anywhere
else on the machine get delayed, dropped, or scrambled (garbled terminal input).
RegisterHotKey only notifies this process when the exact combo fires; it never sees
or touches any other keystroke, so no amount of load here can corrupt typing
elsewhere. Also drops the old admin-privileges requirement `keyboard` needed.

Usage:
    python hotkey_listener.py          # runs until Ctrl+C
"""
import ctypes
from ctypes import wintypes
import json
import sys
import time
from pathlib import Path

# Rebindable hotkeys (2026-08-09) - a real config file rather than a GUI
# rebind tool (which would be a much bigger build for the same practical
# result): edit this file's values by hand and relaunch the listener to
# rebind either hotkey. Missing/malformed file falls back to the same
# defaults that were hardcoded before this existed.
HOTKEY_CONFIG_FILE = Path(r"J:\Jarvis2\hotkeys.json")
DEFAULT_HOTKEYS = {"summon_terminal": "ctrl+alt+j", "summon_avatar": "ctrl+alt+k"}

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001

_MOD_NAMES = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT, "win": MOD_WIN}
_FUNCTION_KEY_BASE = 0x70  # VK_F1


def _vk_for_key(key):
    key = key.strip().lower()
    if len(key) == 1 and (("a" <= key <= "z") or ("0" <= key <= "9")):
        return ord(key.upper())
    if key.startswith("f") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 12:
            return _FUNCTION_KEY_BASE + (n - 1)
    raise ValueError(f"Unsupported hotkey key: {key!r}")


def _parse_hotkey(spec):
    mods = MOD_NOREPEAT
    key_part = None
    for part in spec.split("+"):
        part = part.strip().lower()
        if part in _MOD_NAMES:
            mods |= _MOD_NAMES[part]
        else:
            if key_part is not None:      # 'ctrl+a+b' or an unknown modifier like 'meta+j' used to drop one part silently
                raise ValueError(f"More than one base key (or unknown modifier) in hotkey spec: {spec!r}")
            key_part = part
    if key_part is None:
        raise ValueError(f"No base key found in hotkey spec: {spec!r}")
    return mods, _vk_for_key(key_part)


def _load_hotkey_config():
    try:
        data = json.loads(HOTKEY_CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):      # a list or number in the file would otherwise crash startup
            return dict(DEFAULT_HOTKEYS)
        return {**DEFAULT_HOTKEYS, **data}
    except (OSError, ValueError):      # ValueError covers bad JSON and non-UTF-8 bytes (UnicodeDecodeError)
        return dict(DEFAULT_HOTKEYS)

# Real bug, confirmed 2026-08-08: shelling out to `powershell -File move_window.ps1`
# via subprocess.run spawns powershell.exe as a brand-new console process, and on
# Windows a console app with no console to attach to gets one created for it - that
# transient console is exactly the "small terminal that opens and disappears" that
# was reported live. Fixed by doing the Win32 calls directly in Python (ctypes),
# same technique move_window.ps1 uses, with zero subprocess/console involved at all.
user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
SW_MAXIMIZE = 3
SW_RESTORE = 9
TERMINAL_PROCESS_NAMES = {"WindowsTerminal.exe", "powershell.exe", "pwsh.exe", "cmd.exe", "ConEmu64.exe", "ConEmu.exe"}


def find_terminal_hwnd():
    import psutil

    hwnd_found = None

    def enum_handler(hwnd, _):
        nonlocal hwnd_found
        if hwnd_found is not None:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        try:
            proc = psutil.Process(pid.value)
            name = proc.name()
        except psutil.Error:      # NoSuchProcess and AccessDenied (protected system windows) both mean: not ours, keep looking
            return True
        if name in TERMINAL_PROCESS_NAMES:
            hwnd_found = hwnd
            return False
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(EnumWindowsProc(enum_handler), 0)
    return hwnd_found


def force_foreground(hwnd):
    """Plain SetForegroundWindow is silently ignored by Windows' foreground-lock
    unless the caller is already the foreground process (or acting on real input
    focus) - confirmed 2026-08-08: the window maximized correctly but never actually
    came to front when called from a background listener. Standard workaround:
    briefly attach this thread's input state to the foreground window's owning
    thread, which grants the exception, then detach again immediately after."""
    foreground_hwnd = user32.GetForegroundWindow()
    current_thread_id = kernel32.GetCurrentThreadId()
    foreground_thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None)

    attached = False
    if foreground_thread_id and foreground_thread_id != current_thread_id:
        attached = user32.AttachThreadInput(foreground_thread_id, current_thread_id, True)

    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)

    if attached:
        user32.AttachThreadInput(foreground_thread_id, current_thread_id, False)


def summon_terminal():
    hwnd = find_terminal_hwnd()
    if hwnd:
        user32.ShowWindow(hwnd, SW_RESTORE)
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        force_foreground(hwnd)


def find_avatar_hwnd():
    hwnd_found = None

    def enum_handler(hwnd, _):
        nonlocal hwnd_found
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        if buf.value.startswith("Jarvis Avatar"):
            hwnd_found = hwnd
            return False
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    user32.EnumWindows(EnumWindowsProc(enum_handler), 0)
    return hwnd_found


def summon_avatar():
    # No maximize/restore here (unlike the terminal) - the avatar is a
    # small fixed-size always-on-top window, "summon" just means bring it
    # to actual foreground focus, not change its size/state.
    hwnd = find_avatar_hwnd()
    if hwnd:
        force_foreground(hwnd)


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    keys = _load_hotkey_config()
    actions = {1: summon_terminal, 2: summon_avatar}
    specs = {1: keys["summon_terminal"], 2: keys["summon_avatar"]}

    registered = []
    for hotkey_id, spec in specs.items():
        try:
            mods, vk = _parse_hotkey(spec)
        except (ValueError, AttributeError) as e:      # one bad spec must not stop the other hotkey from registering
            print(f"Warning: skipping malformed hotkey {spec!r}: {e}", file=sys.stderr)
            continue
        if user32.RegisterHotKey(None, hotkey_id, mods, vk):
            registered.append(hotkey_id)
        else:
            print(f"Warning: failed to register hotkey {spec!r} (already in use?)", file=sys.stderr)

    print(f"Jarvis hotkey listener running. {keys['summon_terminal']} summons the terminal, "
          f"{keys['summon_avatar']} summons the avatar. Ctrl+C to quit.")

    msg = wintypes.MSG()
    try:
        while True:
            # PeekMessage instead of blocking GetMessage so Ctrl+C still works when
            # run interactively - each call is essentially free while idle.
            if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                if msg.message == WM_HOTKEY:
                    action = actions.get(msg.wParam)
                    if action:
                        action()
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        for hotkey_id in registered:
            user32.UnregisterHotKey(None, hotkey_id)


if __name__ == "__main__":
    main()

# To autostart at logon:
#   schtasks /create /tn "Jarvis Hotkey Listener" /tr "pythonw.exe J:\Jarvis2\scripts\hotkey_listener.py" /sc onlogon /f
# (pythonw.exe, not python.exe, so no console window stays open. No /rl highest needed
# any more - RegisterHotKey, unlike the old `keyboard` hook, doesn't require elevation.)
