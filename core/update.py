"""Update checker: compares APP_VERSION against the latest GitHub Release.

One-click update: the release carries ``FluxionSetup-<version>.exe`` and
``FluxionSetup-<version>.exe.sha256`` (scripts/build_installer.ps1). The app
downloads the installer, checks it against the checksum (no checksum — no
update) and runs it silently with ``/RESTARTAPP``: the installer closes
Fluxion, replaces the program and starts it again (installer/fluxion.iss).
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RELEASES_URL = "https://api.github.com/repos/djonros/fluxion/releases/latest"


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    release_url: str
    release_notes: str
    is_newer: bool
    installer_url: str = ""  # FluxionSetup-<version>.exe of the release, if any
    installer_name: str = ""
    sha256_url: str = ""  # its .sha256 file


class UpdateError(Exception):
    """A failed one-click update; ``kind``: network, checksum_missing, checksum_mismatch, cancelled."""

    def __init__(self, kind: str, message: str = ""):
        super().__init__(message or kind)
        self.kind = kind


SILENT_ARGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RESTARTAPP")
CHECK_INTERVAL = 24 * 3600  # the automatic check: at most once a day
_SETUP_RE = re.compile(r"^FluxionSetup-(\d+(?:\.\d+){1,3})\.exe$", re.I)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version)[:3])


def is_newer(latest: str, current: str) -> bool:
    try:
        return _version_key(latest) > _version_key(current)
    except Exception:
        return False


def check_for_updates(
    current_version: str,
    releases_url: str | None = None,
    timeout: float = 8.0,
) -> UpdateInfo | None:
    """Fetch the latest release; returns None on any network/parse failure."""
    url = releases_url or os.environ.get("FLUXION_RELEASES_URL", DEFAULT_RELEASES_URL)
    try:
        resp = httpx.get(
            url,
            timeout=timeout,
            headers={"Accept": "application/vnd.github+json"},
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.warning("Update check failed: %s", exc)
        return None

    latest = str(data.get("tag_name", "")).lstrip("vV")
    if not latest:
        return None
    installer, checksum = pick_installer(data.get("assets"), latest)
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest,
        release_url=str(data.get("html_url", "")),
        release_notes=str(data.get("body", ""))[:4000],
        is_newer=is_newer(latest, current_version),
        installer_url=str(installer.get("browser_download_url", "")) if installer else "",
        installer_name=str(installer.get("name", "")) if installer else "",
        sha256_url=str(checksum.get("browser_download_url", "")) if checksum else "",
    )


def pick_installer(assets, version: str = "") -> tuple[dict | None, dict | None]:
    """Return ``(installer, its .sha256)`` assets of a release.

    The installer of *version* wins; otherwise the newest FluxionSetup-*.exe.
    """
    if not isinstance(assets, list):
        return None, None
    by_name = {str(a.get("name", "")): a for a in assets if isinstance(a, dict)}
    setups = [(m.group(1), a) for name, a in by_name.items() if (m := _SETUP_RE.match(name))]
    if not setups:
        return None, None
    exact = [a for v, a in setups if _version_key(v) == _version_key(version)] if version else []
    installer = exact[0] if exact else max(setups, key=lambda item: _version_key(item[0]))[1]
    return installer, by_name.get(str(installer.get("name", "")) + ".sha256")


def parse_sha256(text: str) -> str | None:
    """The hash of a ``sha256sum`` line (``<hash> *<file>``), or None."""
    match = re.search(r"\b([0-9a-fA-F]{64})\b", text or "")
    return match.group(1).lower() if match else None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def should_auto_check(last_check: float | None, now: float | None = None, enabled: bool = True) -> bool:
    """The automatic check runs when it is on and the last one was a day ago or more."""
    if not enabled:
        return False
    now = time.time() if now is None else now
    try:
        last = float(last_check or 0)
    except (TypeError, ValueError):
        last = 0.0
    return last > now or now - last >= CHECK_INTERVAL  # a clock moved back: check again


def download_installer(
    info: UpdateInfo,
    dest_dir: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    timeout: float = 30.0,
) -> Path:
    """Download the installer of *info* into *dest_dir* and check its SHA256.

    Raises UpdateError; a file that does not match is deleted.
    """
    if not info.installer_url:
        raise UpdateError("network", "no installer in the release")
    if not info.sha256_url:
        raise UpdateError("checksum_missing")
    try:
        resp = httpx.get(info.sha256_url, timeout=timeout, follow_redirects=True)
        resp.raise_for_status()
        expected = parse_sha256(resp.text)
    except Exception as exc:
        raise UpdateError("network", str(exc)) from exc
    if not expected:
        raise UpdateError("checksum_missing")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = info.installer_name or f"FluxionSetup-{info.latest_version}.exe"
    target = dest_dir / Path(name).name
    part = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    try:
        with httpx.stream("GET", info.installer_url, timeout=timeout, follow_redirects=True) as stream:
            stream.raise_for_status()
            total = int(stream.headers.get("content-length") or 0)
            done = 0
            with open(part, "wb") as fh:
                for chunk in stream.iter_bytes(1 << 16):
                    if cancelled is not None and cancelled():
                        raise UpdateError("cancelled")
                    fh.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if on_progress is not None:
                        on_progress(done, total)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except Exception as exc:
        part.unlink(missing_ok=True)
        raise UpdateError("network", str(exc)) from exc
    if digest.hexdigest() != expected:
        part.unlink(missing_ok=True)
        raise UpdateError("checksum_mismatch")
    target.unlink(missing_ok=True)
    part.replace(target)
    return target


def launch_installer(path: str | Path) -> subprocess.Popen:
    """Start the installer silently, detached: it closes Fluxion and starts it again."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen([str(path), *SILENT_ARGS], creationflags=flags, close_fds=True)
