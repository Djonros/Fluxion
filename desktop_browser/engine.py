"""Desktop engine bridge: assemble Settings + backend + Assistant for the GUI."""
from __future__ import annotations

from core.config import Settings
from orchestrator import Assistant


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
            provider=create_desktop_search_provider(settings.web),
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
