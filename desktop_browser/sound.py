"""Short sound when Fluxion finishes an answer.

Windows plays assets/message.wav with the standard library (winsound), so the
build needs no Qt multimedia plugins; elsewhere the system beep is used.
"""
from __future__ import annotations

import sys
from pathlib import Path

MESSAGE_SOUND = Path(__file__).resolve().parent.parent / "assets" / "message.wav"


def play_message_sound(path: Path = MESSAGE_SOUND) -> bool:
    """Play the sound without waiting for it; False when nothing could be played."""
    try:
        if sys.platform == "win32" and Path(path).is_file():
            import winsound

            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            return True
        from PySide6.QtWidgets import QApplication

        QApplication.beep()
        return True
    except Exception:  # a missing sound must never break the chat
        return False
