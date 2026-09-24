"""BackendFactory: auto-select and create the appropriate ModelBackend.

Selection order:
    1. ``FLUXION_BACKEND`` env  (ollama | llama_cpp | llamacpp | api)
    2. ``backend:`` key in config.yaml (same values)
    3. ``FLUXION_API_KEY`` set  →  APIBackend
    4. default                   →  OllamaBackend

Fallback chain: if the primary backend ``is_available()`` returns False,
the factory tries the next backend in the chain.
"""
from __future__ import annotations

import logging
import os
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

    return "ollama"


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
