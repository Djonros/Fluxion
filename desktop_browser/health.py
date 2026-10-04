"""Dependency health checks — the backbone of the Zero Silence policy (Phase 14).

Every check returns a DependencyStatus with a human-readable state and a remedy,
so no feature can degrade silently: something is either ✅ available or ⚠️
unavailable *with an explanation and a way to fix it*.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

OLLAMA_INSTALL_URL = "https://ollama.com/download"
DOCKER_INSTALL_URL = "https://www.docker.com/products/docker-desktop/"

_URL_RE = re.compile(r"(https?://[^\s)>]+)")


def linkify(text: str) -> str:
    """Escape *text* and turn bare URLs into clickable anchors."""
    parts = _URL_RE.split(html.escape(text, quote=False))
    return "".join(
        f'<a href="{part}">{part}</a>' if part.startswith("http") else part
        for part in parts
    )


def _docker_daemon_ok(cli: str) -> bool:
    try:
        result = subprocess.run(
            [cli, "info"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


@dataclass
class DependencyStatus:
    key: str
    title: str
    available: bool
    detail: str = ""
    remedy: str = ""
    fix: str = ""  # "" | "searxng" (auto-fix) | "wizard" (open setup wizard)


def check_ollama(host: str) -> DependencyStatus:
    url = f"{host.rstrip('/')}/api/tags"
    try:
        r = httpx.get(url, timeout=5)
        if r.status_code == 200:
            return DependencyStatus("ollama", "Ollama", True, host.rstrip("/"))
        detail = f"HTTP {r.status_code}"
    except httpx.RequestError as exc:
        detail = f"нет соединения ({exc.__class__.__name__})"
    return DependencyStatus(
        "ollama",
        "Ollama",
        False,
        detail,
        f"Установите и запустите Ollama ({OLLAMA_INSTALL_URL}), затем нажмите «Перепроверить».",
    )


def check_model(host: str, model: str) -> DependencyStatus:
    from core.ollama_client import OllamaClient

    client = OllamaClient(host=host, model=model)
    try:
        models = client.list_models()
    except Exception:
        return DependencyStatus(
            "model",
            "Модель",
            False,
            model,
            "Ollama недоступна — сначала запустите Ollama.",
        )
    base = model.split(":")[0]
    matches = [m for m in models if m == model or m.split(":")[0] == base]
    if matches:
        return DependencyStatus("model", "Модель", True, matches[0])
    others = ", ".join(models[:3]) or "нет скачанных моделей"
    return DependencyStatus(
        "model",
        "Модель",
        False,
        f"{model} не скачана (доступно: {others})",
        "Скачайте модель кнопкой ниже или в мастере настройки.",
        fix="wizard",
    )


def detect_engine_backend(settings) -> str:
    """ollama | llama_cpp — what the startup backend resolves to (18.6)."""
    try:
        from core.backend_factory import _detect_backend_type

        return _detect_backend_type(settings)
    except Exception:
        return "ollama"


def check_llamacpp_engine() -> DependencyStatus:
    """Embedded llama.cpp package availability (roadmap 18.5 ships it)."""
    try:
        import llama_cpp  # noqa: F401

        return DependencyStatus("llamacpp", "Движок llama.cpp", True, "встроен, готов")
    except Exception:
        return DependencyStatus(
            "llamacpp",
            "Движок llama.cpp",
            False,
            "пакет llama-cpp-python не установлен",
            "Установите CPU/GPU-пак: pip install -r requirements-llamacpp.txt",
        )


def check_gguf_model(settings) -> DependencyStatus:
    """Chat model for the llama_cpp backend: configured path or catalog default."""
    from core.model_manager import ModelManager

    from core.llama_cpp_backend import resolve_gguf_path

    mm = ModelManager()
    path = resolve_gguf_path(settings) if str(getattr(settings, "gguf_path", "") or "").strip() else ""
    if path:
        if Path(path).is_file():
            return DependencyStatus("model", "Модель", True, Path(path).name)
        return DependencyStatus(
            "model",
            "Модель",
            False,
            f"файл не найден: {path}",
            "Скачайте модель заново на странице «Модели».",
            fix="wizard",
        )
    if mm.is_installed("qwen2.5-coder-7b-q4km"):
        return DependencyStatus(
            "model", "Модель", True, mm.path_for("qwen2.5-coder-7b-q4km")
        )
    return DependencyStatus(
        "model",
        "Модель",
        False,
        "GGUF-модель не скачана",
        "Скачайте модель кнопкой ниже или на странице «Модели».",
        fix="wizard",
    )


def check_docker() -> DependencyStatus:
    """Docker is an optional enhancement (roadmap D1): only SearXNG needs it."""
    path = shutil.which("docker")
    if path and _docker_daemon_ok(path):
        return DependencyStatus("docker", "Docker (опционально)", True, "демон отвечает")
    if path:
        return DependencyStatus(
            "docker",
            "Docker (опционально)",
            True,
            "демон не запущен",
            "Нужен только для SearXNG (расширенный поиск). Запустите Docker Desktop при желании.",
        )
    return DependencyStatus(
        "docker",
        "Docker (опционально)",
        True,
        "не установлен",
        f"Необязательно: нужен только для SearXNG. Установить — {DOCKER_INSTALL_URL}.",
    )


def check_searxng(web_search) -> DependencyStatus:
    tier = getattr(web_search, "provider_tier", "")
    client = getattr(web_search, "client", None)
    url = getattr(client, "base_url", "")
    if tier == "enhanced":
        return DependencyStatus(
            "searxng", "Веб-поиск (SearXNG)", True, f"{url} — расширенный режим"
        )
    if client is None:
        return DependencyStatus(
            "searxng",
            "Веб-поиск",
            False,
            "модуль недоступен",
            "Веб-поиск недоступен в этой сборке.",
        )
    return DependencyStatus(
        "searxng",
        "Веб-поиск (базовый режим)",
        True,
        "встроенный, Docker не нужен",
        "Расширенный поиск (SearXNG в Docker) включается в меню Правка "
        "или кнопкой «Исправить».",
        fix="searxng" if url else "",
    )


def check_rag(rag_service) -> DependencyStatus:
    if rag_service is None:
        return DependencyStatus(
            "rag",
            "Индекс проекта (RAG)",
            False,
            "модуль недоступен",
            "RAG недоступен в этой сборке.",
        )
    try:
        count = int(rag_service.count())
    except Exception:
        count = 0
    if count:
        return DependencyStatus("rag", "Индекс проекта (RAG)", True, f"{count} чанков")
    return DependencyStatus(
        "rag",
        "Индекс проекта (RAG)",
        False,
        "индекс пуст",
        "Откройте страницу «Проект», выберите папку и нажмите «Проиндексировать».",
    )


def check_all(settings, web_search=None, rag_service=None) -> list[DependencyStatus]:
    if detect_engine_backend(settings) == "llama_cpp":
        engine_checks = [
            check_llamacpp_engine(),
            check_gguf_model(settings),
        ]
    else:
        host = getattr(settings, "ollama_host", "http://localhost:11434")
        engine_checks = [
            check_ollama(host),
            check_model(host, getattr(settings, "model", "")),
        ]
    return [
        *engine_checks,
        check_docker(),
        check_searxng(web_search),
        check_rag(rag_service),
    ]


def summary_line(statuses: list[DependencyStatus]) -> str:
    """One-line digest for the startup banner: only the problems."""
    problems = [s.title for s in statuses if not s.available]
    if not problems:
        return ""
    return "Недоступно: " + "; ".join(problems)
