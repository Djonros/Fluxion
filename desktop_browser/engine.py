"""Desktop engine bridge: assemble Settings + backend + Assistant for the GUI."""
from __future__ import annotations

import sys
from pathlib import Path

from core.config import Settings
from orchestrator import Assistant


def _absolutize_data_paths(settings: Settings) -> None:
    """Anchor relative data folders of a frozen build to the exe folder.

    The working directory of an exe is arbitrary (a shortcut, an archiver's
    temp folder), so ``data``, the index and cloned repos must not follow it.
    """
    if not getattr(sys, "frozen", False):
        return
    base = Path(sys.executable).resolve().parent

    def anchored(value: str) -> str:
        path = Path(value)
        if not value or path.is_absolute():
            return value
        return str(base / path)

    settings.paths.data_dir = anchored(settings.paths.data_dir)
    settings.paths.repos_dir = anchored(settings.paths.repos_dir)
    settings.rag.chroma_dir = anchored(settings.rag.chroma_dir)


def _wire_gguf_paths(settings: Settings) -> None:
    """Roadmap 18.2: resolve catalog GGUF paths when llama_cpp backend is active."""
    from core.backend_factory import _detect_backend_type

    if _detect_backend_type(settings) != "llama_cpp":
        return
    from core.model_manager import ModelManager

    mm = ModelManager()
    if not str(getattr(settings, "gguf_path", "") or "").strip():
        chat = mm.path_for("qwen2.5-coder-7b-q4km")
        if chat:
            settings.gguf_path = chat
    rag_gguf = str(getattr(settings.rag, "embedding_gguf", "") or "").strip()
    if not rag_gguf:
        emb = mm.path_for("bge-m3-gguf")
        if emb:
            settings.rag.embedding_gguf = emb


def _searxng_choice() -> bool:
    try:
        from .searxng import searxng_enabled

        return searxng_enabled()
    except Exception:
        return False


def build_engine(settings: Settings | None = None):
    """Return ``(assistant, backend, settings, rag_service, web_search)``.

    Reuses the CLI startup logic (including the multi-model Pro gate).
    RAG and web services are optional: if their heavy dependencies are not
    installed (e.g. slim desktop bundle), they degrade to ``None`` and the
    GUI keeps chat + agent functional.
    """
    from cli.app import _startup_backend

    if settings is None:
        settings = Settings.load()
    _absolutize_data_paths(settings)

    try:
        _wire_gguf_paths(settings)
    except Exception:
        pass

    backend, _gated = _startup_backend(settings)

    rag_service = None
    web_search = None
    try:
        from rag.service import RAGConfig, RAGService

        rag_service = RAGService(
            RAGConfig.from_settings(settings.rag, ollama_host=settings.ollama_host)
        )
    except Exception:
        rag_service = None
    try:
        from web.pipeline import WebSearch

        from .search_embedded import create_desktop_search_provider

        web_search = WebSearch.from_settings(
            settings.web,
            provider=create_desktop_search_provider(
                settings.web, use_searxng=_searxng_choice()
            ),
        )
    except Exception:
        web_search = None

    assistant = Assistant(
        backend,
        settings,
        rag_service=rag_service,
        web_search=web_search,
    )
    return assistant, backend, settings, rag_service, web_search
