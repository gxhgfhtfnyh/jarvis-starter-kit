# -*- coding: utf-8 -*-
"""One-shot test (his ask, Discord DM 1569957563385253939, 2026-11-11 14:10): prove the
scheduled-task mechanism firing an actual Telegram @mention (--mention), not Discord. Owner DM
chat_id is a standing exception (send_gate_hook.py TELEGRAM_STANDING_CHATS), so no approval needed.
Self-deletes its own scheduled task on exit."""
import subprocess
import sys

LOG = r"J:\Jarvis2\logs\oneshot_212pm_tg_mention.log"


def _log(line):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


STICKER_FILE_ID = "CAACAgUAAxkBAAMMavQAAe7y2S_S-VJJefAUqys-b1C-AALtJAAChyChVwecGzPk6ujmPQQ"


def main():
    send = r"J:\Jarvis2\scripts\telegram_bot_send.py"
    r1 = subprocess.run([sys.executable, send, "ping via the scheduler, 2:12",
                        "--reply-to", "6246393956:14", "--mention"], capture_output=True, text=True)
    _log(f"mention: rc={r1.returncode} out={r1.stdout.strip()!r} err={r1.stderr.strip()!r}")
    r2 = subprocess.run([sys.executable, send, "--reply-to", "6246393956:14",
                        "--media-type", "sticker", "--file-id", STICKER_FILE_ID], capture_output=True, text=True)
    _log(f"sticker: rc={r2.returncode} out={r2.stdout.strip()!r} err={r2.stderr.strip()!r}")
    subprocess.run(["schtasks", "/delete", "/tn", "JarvisOneShot_212pmTgMention", "/f"], capture_output=True)


if __name__ == "__main__":
    main()
