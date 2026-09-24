"""Embedder backed by the Ollama embeddings API (model: bge-m3 by default).

Keeps the slim desktop bundle free of torch/sentence-transformers: the
embedding model runs inside the already-required Ollama server.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import httpx
import numpy as np

if TYPE_CHECKING:
    from core.config import RagSettings

logger = logging.getLogger(__name__)


def _ollama_model_name(name: str) -> str:
    """Map HuggingFace-style names ("BAAI/bge-m3") to Ollama tags ("bge-m3")."""
    if name.startswith("hf.co/") or ":" in name:
        return name
    if "/" in name:
        return name.rsplit("/", 1)[-1]
    return name


class Embedder:
    """Embeds texts via POST {host}/api/embed with L2-normalised vectors."""

    def __init__(
        self,
        host: str = "http://localhost:11434",
        model_name: str = "bge-m3",
        device: str = "cpu",
        timeout: float = 300.0,
        batch_size: int = 32,
    ):
        self.host = host.rstrip("/")
        self.model_name = _ollama_model_name(model_name)
        self.backend_id = f"ollama:{self.model_name}"
        self.device = device  # kept for API compatibility; Ollama picks the device
        self.timeout = timeout
        self.batch_size = batch_size
        self._dim: int | None = None

    # -- internals ----------------------------------------------------------

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        try:
            r = httpx.post(
                f"{self.host}/api/embed",
                json={"model": self.model_name, "input": texts},
                timeout=self.timeout,
            )
        except httpx.RequestError as exc:
            raise RuntimeError(
                f"Ollama недоступна по адресу {self.host} ({exc.__class__.__name__}). "
                "Запустите Ollama и повторите индексацию."
            ) from exc
        if r.status_code == 404:
            raise RuntimeError(
                f"Модель эмбеддингов '{self.model_name}' не найдена в Ollama. "
                f"Выполните в терминале: ollama pull {self.model_name}"
            )
        if r.status_code != 200:
            raise RuntimeError(
                f"Ollama /api/embed вернула ошибку ({r.status_code}): {r.text[:200]}"
            )
        embeddings = r.json().get("embeddings")
        if not embeddings:
            raise RuntimeError(
                f"Ollama вернула пустой ответ эмбеддингов для модели '{self.model_name}'"
            )
        return np.asarray(embeddings, dtype=np.float32)

    # -- public API ----------------------------------------------------------

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._dim = int(self._embed_batch(["dimension probe"]).shape[1])
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts; returns L2-normalised float32 array (N, dim)."""
        if not texts:
            return np.zeros((0, self._dim or 0), dtype=np.float32)
        parts: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors = self._embed_batch(batch)
            if self._dim is None:
                self._dim = int(vectors.shape[1])
            parts.append(vectors)
        out = np.vstack(parts)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (out / norms).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]

    @classmethod
    def from_settings(
        cls,
        settings: "RagSettings",
        host: str = "http://localhost:11434",
    ) -> "Embedder":
        return cls(host=host, model_name=settings.embedding_model, device=settings.embedding_device)


class LlamaCppEmbedder:
    """Embeds texts in-process via llama-cpp-python (GGUF embedding model).

    Roadmap 18.3: keeps RAG working without an Ollama server; the model
    (bge-m3 GGUF by default) runs inside the app process.
    """

    def __init__(
        self,
        model_path: str,
        model_name: str = "bge-m3",
        n_gpu_layers: int = 0,
        batch_size: int = 32,
        llm=None,
        runtime=None,
        runtime_key: str = "embedding",
    ):
        self.model_path = str(model_path)
        self.model_name = model_name or "bge-m3"
        self.backend_id = f"llama_cpp:{self.model_name}"
        self.batch_size = batch_size
        self._dim: int | None = None
        if llm is None:
            try:
                from llama_cpp import Llama
            except ImportError as exc:
                raise ImportError(
                    "llama-cpp-python не установлен: pip install llama-cpp-python "
                    "(см. requirements-llamacpp.txt)"
                ) from exc
            self._llm = Llama(
                model_path=self.model_path,
                embedding=True,
                n_gpu_layers=n_gpu_layers,
                verbose=False,
            )
        else:
            self._llm = llm
        from core.model_runtime import get_shared_runtime

        self._runtime = runtime or get_shared_runtime()
        self._runtime_key = runtime_key
        self._runtime.register(
            runtime_key, self._llm, n_gpu_layers=n_gpu_layers, task="embedding"
        )

    def unload(self) -> None:
        self._runtime.unregister(self._runtime_key)

    @classmethod
    def from_config(cls, config) -> "LlamaCppEmbedder":
        import os

        model_path = str(getattr(config, "embedding_gguf", "") or "").strip()
        if not model_path:
            model_path = os.environ.get("FLUXION_EMBED_GGUF", "")
        if not model_path:
            raise RuntimeError(
                "Путь к GGUF-модели эмбеддингов не задан: укажите rag.embedding_gguf "
                "в config.yaml или переменную FLUXION_EMBED_GGUF."
            )
        return cls(
            model_path=model_path,
            model_name=getattr(config, "embedding_model", "bge-m3") or "bge-m3",
        )

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        response = self._llm.create_embedding(input=list(texts))
        data = response.get("data") or []
        vectors = [item.get("embedding", []) for item in data]
        if not vectors or not any(vectors):
            raise RuntimeError(
                f"llama.cpp вернул пустые эмбеддинги для модели '{self.model_name}'"
            )
        return np.asarray(vectors, dtype=np.float32)

    @property
    def dimension(self) -> int:
        if self._dim is None:
            self._dim = int(self._embed_batch(["dimension probe"]).shape[1])
        return self._dim

    def embed(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts; returns L2-normalised float32 array (N, dim)."""
        if not texts:
            return np.zeros((0, self._dim or 0), dtype=np.float32)
        parts: list[np.ndarray] = []
        for start in range(0, len(texts), self.batch_size):
            batch = texts[start : start + self.batch_size]
            vectors = self._embed_batch(batch)
            if self._dim is None:
                self._dim = int(vectors.shape[1])
            parts.append(vectors)
        out = np.vstack(parts)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (out / norms).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self.embed([text])[0]
