"""Child processes of the desktop app: no console windows on Windows."""
from __future__ import annotations

import os
import subprocess

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)


def no_window() -> dict[str, int]:
    """Return ``subprocess`` keyword arguments that hide a console program's window.

    A windowed exe that starts git, pip, winget or Python gets a black console
    window per call; with these flags the child runs without one. Elsewhere the
    result is empty, so callers pass it unconditionally: ``run(cmd, **no_window())``.
    """
    if os.name != "nt":
        return {}
    return {"creationflags": _CREATE_NO_WINDOW}
