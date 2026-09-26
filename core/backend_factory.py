"""BackendFactory: auto-select and create the appropriate ModelBackend.

Selection order:
    1. ``FLUXION_BACKEND`` env  (ollama | llama_cpp | llamacpp | api)
    2. ``backend:`` key in config.yaml (same values)
    3. ``FLUXION_API_KEY`` set  →  APIBackend
    4. llama-cpp-python installed and a local GGUF model present
       (``gguf_path`` / ``FLUXION_GGUF_PATH`` or the chat model installed via
       the "Models" page)      →  LlamaCppBackend
    5. default                   →  OllamaBackend

Fallback chain: if the primary backend ``is_available()`` returns False,
the factory tries the next backend in the chain.
"""
from __future__ import annotations

import importlib.util
import logging
import os
from pathlib import Path
from typing import Any

from .config import Settings
from .inference import ModelBackend, OllamaBackend

logger = logging.getLogger(__name__)


class BackendFactory:
    """Creates the best available backend from settings and environment."""

    @staticmethod
    def create(settings: Settings) -> ModelBackend:
        """Return the first available backend from the configured + fallback chain."""
        backend_type = _detect_backend_type(settings)
        chain = _build_chain(backend_type)

        errors: list[str] = []
        for kind in chain:
            try:
                backend = _instantiate(kind, settings)
                if backend.is_available():
                    logger.info("Backend selected: %s", kind)
                    return backend
                errors.append(f"{kind}: not available")
            except Exception as exc:
                errors.append(f"{kind}: {exc}")

        raise RuntimeError(
            "No backend available.\n" + "\n".join(f"  - {e}" for e in errors)
        )

    @staticmethod
    def create_primary(settings: Settings) -> ModelBackend:
        """Create the configured backend without fallback (may be unavailable)."""
        backend_type = _detect_backend_type(settings)
        return _instantiate(backend_type, settings)


# ── internals ────────────────────────────────────────────────────────────────

def _detect_backend_type(settings: Settings) -> str:
    """Determine backend type from env, config.yaml, or api-key heuristic."""
    explicit = os.environ.get("FLUXION_BACKEND", "").strip().lower()
    if explicit:
        normalized = "llama_cpp" if explicit in ("llama_cpp", "llamacpp") else explicit
        if normalized in ("ollama", "api", "llama_cpp"):
            return normalized

    configured = str(getattr(settings, "backend", "") or "").strip().lower()
    if configured:
        normalized = "llama_cpp" if configured in ("llama_cpp", "llamacpp") else configured
        if normalized in ("ollama", "api", "llama_cpp"):
            return normalized

    api_key = os.environ.get("FLUXION_API_KEY", "")
    if api_key:
        return "api"

    # The embedded engine is the documented default of the builds: use it
    # whenever it can actually run, instead of silently requiring Ollama.
    if _llama_cpp_installed() and _local_gguf_available(settings):
        return "llama_cpp"

    return "ollama"


# Chat model the desktop app resolves when gguf_path is empty
# (desktop_browser.engine._wire_gguf_paths).
_DEFAULT_CHAT_MODEL_ID = "qwen2.5-coder-7b-q4km"


def _llama_cpp_installed() -> bool:
    try:
        return importlib.util.find_spec("llama_cpp") is not None
    except (ImportError, ValueError):
        return False


def _local_gguf_available(settings: Settings) -> bool:
    """True if a GGUF chat model is present locally (no side effects)."""
    explicit = str(getattr(settings, "gguf_path", "") or "").strip() or os.environ.get(
        "FLUXION_GGUF_PATH", ""
    ).strip()
    if explicit:
        return Path(explicit).is_file()
    try:
        from .model_manager import MODEL_CATALOG, _default_models_dir

        entry = next((m for m in MODEL_CATALOG if m.model_id == _DEFAULT_CHAT_MODEL_ID), None)
        return entry is not None and (_default_models_dir() / entry.filename).is_file()
    except Exception:
        return False


def _build_chain(primary: str) -> list[str]:
    """Build fallback chain starting with *primary*."""
    chain = [primary]
    for kind in ("ollama", "api", "llama_cpp"):
        if kind not in chain:
            chain.append(kind)
    return chain


def _instantiate(kind: str, settings: Settings) -> ModelBackend:
    if kind == "ollama":
        return OllamaBackend(settings)
    if kind == "api":
        from .api_backend import APIBackend
        return APIBackend.from_settings(settings)
    if kind == "llama_cpp":
        from .llama_cpp_backend import LlamaCppBackend
        return LlamaCppBackend.from_settings(settings)
    raise ValueError(f"Unknown backend type: {kind}")
