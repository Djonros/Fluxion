"""Local license activation storage (offline, single license file)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .models import License, LicenseError
from .verifier import verify_license


def _default_license_path() -> Path:
    """Frozen builds persist activation per-user in %APPDATA%; dev runs use data/.

    Trainer subprocesses spawned by a frozen app (venv python, not frozen)
    locate the same activation via FLUXION_LICENSE_FILE or the %APPDATA%
    fallback when the bundle-relative key is absent.
    """
    override = os.environ.get("FLUXION_LICENSE_FILE")
    if override:
        return Path(override)
    if getattr(sys, "frozen", False):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "Fluxion" / "license.key"
    local = Path(__file__).resolve().parents[1] / "data" / "license.key"
    if not local.exists():
        appdata = os.environ.get("APPDATA")
        if appdata:
            shared = Path(appdata) / "Fluxion" / "license.key"
            if shared.exists():
                return shared
    return local


DEFAULT_LICENSE_PATH = _default_license_path()


def _bundled_license_key() -> str | None:
    """Read a license key shipped inside the frozen bundle, if any."""
    if not getattr(sys, "frozen", False):
        return None
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(sys.executable)
    bundled = Path(base) / "data" / "license.key"
    try:
        key = bundled.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not key:
        return None
    try:
        verify_license(key)
    except LicenseError:
        return None
    return key


def _migrate_bundled(target: Path) -> None:
    """Seed a fresh user-writable activation from a bundled key (frozen only)."""
    key = _bundled_license_key()
    if key is None:
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(key + "\n", encoding="utf-8")
    except OSError:
        pass


def activate(key: str, path: Path | None = None) -> License:
    """Verify the key offline and persist it as the local activation."""
    license_obj = verify_license(key)
    target = Path(path) if path is not None else DEFAULT_LICENSE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(key.strip() + "\n", encoding="utf-8")
    return license_obj


def load_activation(path: Path | None = None) -> License | None:
    """Return the verified License stored locally, or None if absent/invalid."""
    target = Path(path) if path is not None else DEFAULT_LICENSE_PATH
    if not target.exists():
        _migrate_bundled(target)
    if not target.exists():
        return None
    key = target.read_text(encoding="utf-8").strip()
    if not key:
        return None
    try:
        return verify_license(key)
    except LicenseError:
        return None


def clear_activation(path: Path | None = None) -> bool:
    """Remove the stored activation. Returns True if a file was removed."""
    target = Path(path) if path is not None else DEFAULT_LICENSE_PATH
    if target.exists():
        target.unlink()
        return True
    return False
