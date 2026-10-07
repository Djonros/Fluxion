"""Where the desktop app runs from: detection of unsuitable locations."""
from __future__ import annotations

import tempfile
from pathlib import Path

TEMP_RUN_MARKERS = ("rar$", "\\temp\\", "$recyble", "$recycle")
TEMP_RUN_WARNING = (
    "Программа запущена из временной папки (архив не распакован). "
    "Распакуйте Fluxion в папку на диске: иначе данные, окружение обучения "
    "и модели будут потеряны."
)


def _normalized(path: str | Path) -> str:
    return str(path).replace("/", "\\").lower()


def is_temp_run(exe_path: str | Path) -> bool:
    """Tell whether *exe_path* lies in a temporary folder.

    That is the case when the archive was opened in an archiver and the exe
    started straight from it, or the program sits in the recycle bin.
    """
    normalized = _normalized(exe_path)
    if any(marker in normalized for marker in TEMP_RUN_MARKERS):
        return True
    temp_dir = _normalized(tempfile.gettempdir()).rstrip("\\")
    return bool(temp_dir) and normalized.startswith(temp_dir + "\\")
