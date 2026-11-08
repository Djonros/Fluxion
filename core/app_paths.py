"""Where Fluxion keeps the user's data: chats, indexes, logs, user config.

* From sources: the repository root (``<repo>/data``, ``<repo>/config``), as before.
* Installed/frozen build: ``%LOCALAPPDATA%\\Fluxion`` (``data``, ``config``).
  The program folder is replaced by every update and may be read-only, so
  nothing the user makes lives there. LOCALAPPDATA, not APPDATA: indexes and
  cloned repositories grow to gigabytes and must not travel with a roaming
  profile; models and the training environment already live there too.
  Small per-user settings (licence, trial, cloud API, materials) stay in
  ``%APPDATA%\\Fluxion`` as before.
* Portable mode: a file ``portable.txt`` next to the exe keeps everything in
  the exe folder (the old behaviour), e.g. for a copy on a USB stick.

``FLUXION_HOME`` overrides the root of an installed build.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

APP_NAME = "Fluxion"
PORTABLE_MARKER = "portable.txt"
MIGRATION_MARKER = ".migrated.json"
# the training environment is a venv (several GB, absolute paths inside):
# it is not copied; training.training_env_dir() still finds it in the old place
_MIGRATION_SKIP = {"training_env", "__pycache__"}


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def source_root() -> Path:
    return Path(__file__).resolve().parents[1]


def is_portable() -> bool:
    """A frozen build with ``portable.txt`` next to the exe."""
    return is_frozen() and (exe_dir() / PORTABLE_MARKER).is_file()


_PACKAGE_UNKNOWN = object()
_package_name_cache: object = _PACKAGE_UNKNOWN
APPMODEL_ERROR_NO_PACKAGE = 15700
ERROR_INSUFFICIENT_BUFFER = 122


def _query_package_full_name() -> str:
    """Package full name of this process (MSIX), "" outside a package or off Windows."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes

        func = ctypes.windll.kernel32.GetCurrentPackageFullName  # Windows 8+
        func.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        func.restype = wintypes.LONG
        length = wintypes.UINT(0)
        rc = func(ctypes.byref(length), None)
        if rc != ERROR_INSUFFICIENT_BUFFER or not length.value:
            return ""  # APPMODEL_ERROR_NO_PACKAGE: an ordinary process
        buf = ctypes.create_unicode_buffer(length.value)
        if func(ctypes.byref(length), buf) != 0:
            return ""
        return buf.value
    except (OSError, AttributeError, ValueError):
        return ""


def package_full_name() -> str:
    """MSIX package of this run (the Microsoft Store build), "" otherwise. Cached.

    ``FLUXION_STORE_BUILD=1`` pretends to be packaged: checking the Store
    behaviour (no in-app updater) without installing the package.
    """
    global _package_name_cache
    if os.environ.get("FLUXION_STORE_BUILD") == "1":
        return "Fluxion.Test_0.0.0.0_x64__dev"
    if _package_name_cache is _PACKAGE_UNKNOWN:
        _package_name_cache = _query_package_full_name()
    return str(_package_name_cache)


def is_store_package() -> bool:
    """The copy from the Microsoft Store (an MSIX package): the Store updates it.

    Its program folder (``WindowsApps``) is read-only, the in-app updater and
    FluxionSetup.exe must not touch it, and the HKCU Run key is virtualized.
    """
    return bool(package_full_name())


def is_installed() -> bool:
    """A copy put in place by FluxionSetup.exe (its uninstaller is next to the exe).

    Only such a copy updates itself in one click; a portable or unpacked .7z
    copy, the Microsoft Store copy and a run from the sources are not.
    """
    if not is_frozen() or is_portable() or is_store_package():
        return False
    return any(exe_dir().glob("unins*.exe"))


def user_root() -> Path:
    """Per-user folder of an installed build: ``%LOCALAPPDATA%\\Fluxion``."""
    override = os.environ.get("FLUXION_HOME", "").strip()
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / APP_NAME


def app_home() -> Path:
    """Folder that holds ``data`` and ``config`` of this run."""
    if not is_frozen():
        return source_root()
    if is_portable():
        return exe_dir()
    return user_root()


def data_dir() -> Path:
    return app_home() / "data"


def anchor(value: str) -> str:
    """Anchor a relative path from the settings (``data``, ``data/chroma``) to app_home.

    From sources it is left as is: it is relative to the working directory,
    the repository root.
    """
    if not value or Path(value).is_absolute() or not is_frozen():
        return value
    return str(app_home() / value)


def user_config_path() -> Path | None:
    """``config/config.yaml`` the user keeps next to the data (frozen builds), if any."""
    if not is_frozen():
        return None
    path = app_home() / "config" / "config.yaml"
    return path if path.is_file() else None


def _copy_tree(src: Path, dst: Path) -> None:
    for item in src.iterdir():
        if item.name in _MIGRATION_SKIP:
            continue
        target = dst / item.name
        if item.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_tree(item, target)
        elif not target.exists():
            shutil.copy2(item, target)


def migrate_legacy_data() -> list[str]:
    """Copy ``data`` and ``config`` from the exe folder to the user folder once.

    Earlier frozen builds kept them next to the exe. Runs only for an
    installed (non-portable) frozen build, only when the new ``data`` does not
    exist yet; the old folders are left in place. Returns the copied folder
    names; never raises (a failed copy must not stop the program).
    """
    if not is_frozen() or is_portable():
        return []
    old_home, new_home = exe_dir(), user_root()
    try:
        if old_home.resolve() == new_home.resolve():
            return []
        new_data = new_home / "data"
        if (new_data / MIGRATION_MARKER).exists() or new_data.exists():
            return []
        copied: list[str] = []
        for name in ("data", "config"):
            src = old_home / name
            if not src.is_dir():
                continue
            dst = new_home / name
            if name == "config" and dst.exists():
                continue
            dst.mkdir(parents=True, exist_ok=True)
            _copy_tree(src, dst)
            copied.append(name)
        if not copied:
            return []
        new_data.mkdir(parents=True, exist_ok=True)
        (new_data / MIGRATION_MARKER).write_text(
            json.dumps(
                {"from": str(old_home), "copied": copied, "time": time.strftime("%Y-%m-%d %H:%M:%S")},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return copied
    except OSError:
        return []
