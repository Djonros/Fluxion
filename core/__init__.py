"""Core: model abstraction layer (config, Ollama client, inference backend)."""

from .config import Settings, GenerationSettings, RagSettings, WebSettings, PathsSettings
from .inference import ModelBackend, OllamaBackend
from .api_backend import APIBackend
from .backend_factory import BackendFactory
from .ollama_client import OllamaClient, OllamaError
from .tokenizer_util import count_tokens, truncate_to_tokens

__all__ = [
    "Settings",
    "GenerationSettings",
    "RagSettings",
    "WebSettings",
    "PathsSettings",
    "ModelBackend",
    "OllamaBackend",
    "APIBackend",
    "BackendFactory",
    "OllamaClient",
    "OllamaError",
    "count_tokens",
    "truncate_to_tokens",
]
