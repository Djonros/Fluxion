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


# ── Activation from a key file (simpler than copy-paste) ──────────────────

MAX_KEY_FILE_BYTES = 64 * 1024
KEY_FILE_SUFFIXES = (".key", ".txt", ".lic")
_KEY_RE = None


def _key_candidates(text: str) -> list[str]:
    """Possible keys in arbitrary text: a key file, an e-mail body, a key
    wrapped over several lines by a mail client.

    Consecutive whitespace-separated pieces made only of key characters are
    joined — that reassembles a wrapped key, while ordinary words around it
    ("Ваш ключ:", "Спасибо!") break the run and stay out of it."""
    import re

    global _KEY_RE
    if _KEY_RE is None:
        _KEY_RE = re.compile(r"[A-Za-z0-9_\-+/=]{16,}\.[A-Za-z0-9_\-+/=]{16,}")
    piece = re.compile(r"^[A-Za-z0-9_\-+/=.]+$")
    text = (text or "").replace("\ufeff", "")
    candidates: list[str] = []
    run: list[str] = []
    for token in text.split() + [" "]:          # sentinel flushes the last run
        if piece.match(token):
            run.append(token)
            continue
        if run:
            joined = "".join(run)
            if "." in joined:
                candidates.append(joined)
                candidates.extend(_KEY_RE.findall(joined))
            run = []
    candidates.extend(_KEY_RE.findall(text))
    seen: dict[str, None] = {}
    for candidate in candidates:
        seen.setdefault(candidate, None)
    return list(seen)


def extract_key(text: str) -> str:
    """The first valid licence key found in *text*.

    Raises LicenseError with the most useful reason (expired, other device…)
    when a key is present but cannot be activated."""
    from .models import LicenseError

    last_error: LicenseError | None = None
    for candidate in _key_candidates(text):
        try:
            verify_license(candidate)
            return candidate
        except LicenseError as exc:
            if "." in candidate and len(candidate) > 40:
                last_error = exc
    if last_error is not None and "malformed" not in str(last_error):
        raise last_error
    raise LicenseError("лицензионный ключ Fluxion не найден")


def activate_text(text: str, path: Path | None = None) -> License:
    """Activate from pasted text (whitespace, line breaks, BOM tolerated)."""
    return activate(extract_key(text), path)


def read_key_file(file_path: Path | str) -> str:
    from .models import LicenseError

    file_path = Path(file_path)
    try:
        if file_path.stat().st_size > MAX_KEY_FILE_BYTES:
            raise LicenseError("файл слишком большой для лицензионного ключа")
        return file_path.read_text(encoding="utf-8-sig", errors="replace")
    except FileNotFoundError:
        raise LicenseError(f"файл не найден: {file_path}") from None
    except OSError as exc:
        raise LicenseError(f"не удалось прочитать файл: {exc}") from None


def activate_file(file_path: Path | str, path: Path | None = None) -> License:
    """Activate from a key file, e.g. the one issue-key.bat produces."""
    return activate_text(read_key_file(file_path), path)


def key_search_dirs() -> list[Path]:
    """Where a user is likely to have saved a key file."""
    dirs: list[Path] = []
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    else:
        dirs.append(Path(__file__).resolve().parent.parent)
    home = Path.home()
    for name in ("Downloads", "Загрузки", "Desktop", "Рабочий стол"):
        dirs.append(home / name)
    seen: dict[str, Path] = {}
    for d in dirs:
        seen.setdefault(str(d).lower(), d)
    return [d for d in seen.values() if d.is_dir()]


def find_key_files(dirs: list[Path] | None = None, limit_per_dir: int = 200) -> list[Path]:
    """Valid, not yet activated key files in *dirs*, newest first.

    Only the top level of each folder is scanned, files are small and every
    candidate must pass the offline signature check."""
    current = None
    try:
        if DEFAULT_LICENSE_PATH.exists():
            current = DEFAULT_LICENSE_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        pass
    found: list[tuple[float, Path]] = []
    for folder in dirs if dirs is not None else key_search_dirs():
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)[:5000]
        except OSError:
            continue
        checked = 0
        for item in entries:
            if checked >= limit_per_dir:
                break
            if item.suffix.lower() not in KEY_FILE_SUFFIXES:
                continue
            try:
                stat = item.stat()
                if not item.is_file() or stat.st_size > MAX_KEY_FILE_BYTES:
                    continue
                checked += 1
                key = extract_key(read_key_file(item))
            except Exception:
                continue
            if key != current:
                found.append((stat.st_mtime, item))
    return [p for _, p in sorted(found, key=lambda t: t[0], reverse=True)]
