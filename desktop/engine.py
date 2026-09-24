"""Desktop engine bridge: assemble Settings + backend + Assistant for the GUI."""
from __future__ import annotations

from core.config import Settings
from orchestrator import Assistant


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

        web_search = WebSearch.from_settings(settings.web)
    except Exception:
        web_search = None

    assistant = Assistant(
        backend,
        settings,
        rag_service=rag_service,
        web_search=web_search,
    )
    return assistant, backend, settings, rag_service, web_search
