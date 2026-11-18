# -*- coding: utf-8 -*-
"""
Native Windows balloon-tip notification, zero new Python dependencies --
reuses the same System.Windows.Forms.NotifyIcon technique already proven
in run_scheduled_prompt.ps1's Show-FailureToast, just shelled out from
Python via a short-lived PowerShell subprocess.

Usage: from win_toast import show_toast; show_toast("Jarvis", "Reply ready")
"""
import subprocess

_PS_TEMPLATE = r"""
Add-Type -AssemblyName System.Windows.Forms
$ni = New-Object System.Windows.Forms.NotifyIcon
$ni.Icon = [System.Drawing.SystemIcons]::Information
$ni.Visible = $true
$ni.BalloonTipTitle = "{title}"
$ni.BalloonTipText = "{text}"
$ni.ShowBalloonTip(6000)
Start-Sleep -Seconds 6
$ni.Dispose()
"""


def show_toast(title, text):
    title = (title or "").replace('"', "'")[:64]
    text = (text or "").replace('"', "'")[:200]
    script = _PS_TEMPLATE.format(title=title, text=text)
    subprocess.Popen(
        ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


if __name__ == "__main__":
    show_toast("Jarvis", "Test toast notification")
