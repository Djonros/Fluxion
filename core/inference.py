"""Model backend abstraction layer.

Phase 1: OllamaBackend (local, via Ollama server).
Phase 12: APIBackend (cloud, OpenAI-compatible), LlamaCppBackend (direct GGUF).
Use BackendFactory.create() to auto-select the best available backend.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from .config import GenerationSettings, Settings
from .ollama_client import OllamaClient, OllamaError


class ModelBackend(ABC):
    """Interface for text generation backends."""

    @abstractmethod
    def generate(
        self, messages: list[dict[str, str]], gen: GenerationSettings | None = None
    ) -> str: ...

    @abstractmethod
    def stream(
        self, messages: list[dict[str, str]], gen: GenerationSettings | None = None
    ) -> Iterator[str]: ...

    @abstractmethod
    def is_available(self) -> bool: ...


class OllamaBackend(ModelBackend):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = OllamaClient(
            host=settings.ollama_host, model=settings.model
        )

    def _options(self, gen: GenerationSettings | None) -> dict:
        g = gen or self.settings.generation
        return {
            "temperature": g.temperature,
            "top_p": g.top_p,
            "num_predict": g.max_tokens,
            "num_ctx": g.num_ctx,
        }

    def generate(self, messages, gen=None) -> str:
        try:
            return self.client.chat(messages, options=self._options(gen))
        except OllamaError:
            raise

    def stream(self, messages, gen=None) -> Iterator[str]:
        yield from self.client.stream_chat(messages, options=self._options(gen))

    def is_available(self) -> bool:
        return self.client.is_alive() and self.client.exists()
