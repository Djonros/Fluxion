"""Dependency auto-install for the setup wizard (clean-machine experience).

Everything installable via PowerShell/winget is installed silently; the
Docker daemon is started via Docker Desktop when the CLI exists but the
engine is down.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from core.proc import no_window

logger = logging.getLogger(__name__)

WINGET_PACKAGES = {
    "ollama": "Ollama.Ollama",
    "docker": "Docker.DockerDesktop",
    "git": "Git.Git",
}


def _docker_fallbacks() -> tuple[Path, ...]:
    programfiles = Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
    return (
        programfiles / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
    )


def winget_available() -> bool:
    return shutil.which("winget") is not None


def winget_install(package_id: str, on_line=None) -> tuple[bool, str]:
    """Silently install *package_id* via winget, streaming output lines."""
    winget = shutil.which("winget")
    if winget is None:
        return False, "winget не найден в PATH"
    cmd = [
        winget,
        "install",
        "--id",
        package_id,
        "-e",
        "--silent",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--disable-interactivity",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            **no_window(),
        )
    except OSError as exc:
        return False, f"не удалось запустить winget: {exc}"
    chunks: list[str] = []
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip()
        if not line:
            continue
        chunks.append(line)
        if on_line is not None:
            on_line(line)
    code = proc.wait()
    return code == 0, "\n".join(chunks)


def docker_cli() -> str | None:
    found = shutil.which("docker")
    if found:
        return found
    for candidate in _docker_fallbacks():
        if candidate.exists():
            return str(candidate)
    return None


def docker_daemon_ok(timeout: float = 12.0) -> bool:
    """True when the docker CLI exists and the engine answers ``docker info``."""
    cli = docker_cli()
    if cli is None:
        return False
    try:
        result = subprocess.run(
            [cli, "info"], capture_output=True, text=True, timeout=timeout, **no_window()
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _docker_desktop_exe() -> Path | None:
    candidates = [
        Path(os.environ.get("PROGRAMFILES") or r"C:\Program Files")
        / "Docker"
        / "Docker"
        / "Docker Desktop.exe",
        Path(os.environ.get("LOCALAPPDATA") or "")
        / "Docker"
        / "Docker Desktop.exe",
    ]
    for candidate in candidates:
        if candidate.name and candidate.exists():
            return candidate
    return None


def ensure_docker(wait_secs: float = 120.0, on_status=None) -> bool:
    """Make the docker daemon respond, launching Docker Desktop if needed."""
    if docker_daemon_ok():
        return True
    exe = _docker_desktop_exe()
    if exe is None:
        logger.info("Docker Desktop executable not found")
        return False
    try:
        subprocess.Popen([str(exe)], close_fds=True)
    except OSError as exc:
        logger.warning("failed to launch Docker Desktop: %s", exc)
        return False
    if on_status is not None:
        on_status("Ожидаем запуск движка Docker…")
    deadline = time.monotonic() + wait_secs
    while time.monotonic() < deadline:
        time.sleep(3.0)
        if docker_daemon_ok():
            return True
    return docker_daemon_ok()


def _read_path_key(root, subkey: str) -> str:
    import winreg

    with winreg.OpenKey(root, subkey) as key:
        value, _type = winreg.QueryValueEx(key, "Path")
    return os.path.expandvars(value) if isinstance(value, str) else ""


def refresh_path() -> bool:
    """Reload PATH from the registry so freshly installed CLIs become visible."""
    try:
        import winreg
    except ImportError:
        return False
    try:
        machine = _read_path_key(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        )
        user = _read_path_key(winreg.HKEY_CURRENT_USER, r"Environment")
    except OSError:
        return False
    combined = ";".join(part for part in (machine, user) if part)
    if combined:
        os.environ["PATH"] = combined
        return True
    return False
