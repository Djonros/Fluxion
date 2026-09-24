"""Auto-start a local SearXNG container so web search works out of the box."""
from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CONTAINER_NAME = "fluxion-searxng"
IMAGE = "searxng/searxng"

_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1"})

_SETTINGS_TEMPLATE = """\
use_default_settings: true
server:
  secret_key: "{secret}"
  limiter: false
search:
  formats:
    - html
    - json
"""


def _data_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    data = base / "data" / "searxng"
    data.mkdir(parents=True, exist_ok=True)
    return data


def _write_settings() -> Path:
    path = _data_dir() / "settings.yml"
    if not path.exists():
        path.write_text(_SETTINGS_TEMPLATE.format(secret=secrets.token_hex(16)), encoding="utf-8")
    return path


def _local_port(base_url: str) -> int | None:
    try:
        parsed = urlparse(base_url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", ""):
        return None
    host = (parsed.hostname or "").lower().strip("[]")
    if host not in _LOCAL_HOSTS:
        return None
    return parsed.port or 80


def _docker(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=180)


def ensure_searxng(base_url: str, wait_secs: float = 45.0) -> bool:
    from web.searxng import SearXNGClient

    client = SearXNGClient(base_url=base_url)
    if client.is_alive():
        return True

    port = _local_port(base_url)
    docker = shutil.which("docker")
    if port is None or docker is None:
        logger.info("SearXNG autostart skipped (docker=%s, url=%s)", bool(docker), base_url)
        return False

    try:
        info = _docker([docker, "info"])
        daemon_ok = info.returncode == 0
    except (OSError, subprocess.SubprocessError):
        daemon_ok = False
    if not daemon_ok:
        from .installer import ensure_docker

        logger.info("docker daemon down; launching Docker Desktop")
        if not ensure_docker():
            logger.warning("docker daemon did not come up; cannot start SearXNG")
            return False

    try:
        started = _docker([docker, "start", CONTAINER_NAME])
        if started.returncode != 0:
            created = _docker(
                [
                    docker,
                    "run",
                    "-d",
                    "--name",
                    CONTAINER_NAME,
                    "-p",
                    f"{port}:8080",
                    "-e",
                    f"SEARXNG_BASE_URL={base_url.rstrip('/')}/",
                    "-v",
                    f"{_write_settings()}:/etc/searxng/settings.yml:ro",
                    IMAGE,
                ]
            )
            if created.returncode != 0:
                detail = (created.stderr or created.stdout or "").strip()
                logger.warning("docker run %s failed: %s", IMAGE, detail)
                return False
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("SearXNG autostart error: %s", exc)
        return False

    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if client.is_alive():
            logger.info("SearXNG available at %s", base_url)
            return True
    logger.warning("SearXNG did not become healthy at %s", base_url)
    return False
