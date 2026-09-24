"""Update checker: compares APP_VERSION against the latest GitHub Release.

v1.0 scope (roadmap 17.2): check + notify only — no silent auto-download.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

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
    return UpdateInfo(
        current_version=current_version,
        latest_version=latest,
        release_url=str(data.get("html_url", "")),
        release_notes=str(data.get("body", ""))[:4000],
        is_newer=is_newer(latest, current_version),
    )
