"""RAGService: ties together chunker, embedder, store, indexer, retriever.

All heavy components (embedding model, ChromaDB) are lazily initialised
on first use so the CLI starts instantly.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .chunker import TreeSitterChunker
from .embedder import Embedder
from .indexer import Indexer
from .retriever import RetrievalResult, Retriever
from .store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass
class RAGConfig:
    embedding_model: str = "bge-m3"
    embedding_device: str = "cpu"
    embedding_backend: str = ""  # "" (auto) | ollama | llama_cpp
    embedding_gguf: str = ""
    ollama_host: str = "http://localhost:11434"
    chroma_dir: str = "data/chroma"
    chunk_max_chars: int = 1500
    chunk_overlap: int = 200
    top_k: int = 8
    use_rerank: bool = True

    @classmethod
    def from_settings(
        cls,
        rag: "RagSettings",
        ollama_host: str | None = None,
    ) -> "RAGConfig":
        return cls(
            embedding_model=rag.embedding_model,
            embedding_device=rag.embedding_device,
            embedding_backend=getattr(rag, "embedding_backend", ""),
            embedding_gguf=getattr(rag, "embedding_gguf", ""),
            ollama_host=ollama_host
            or os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            chroma_dir=rag.chroma_dir,
            chunk_max_chars=rag.chunk_max_chars,
            chunk_overlap=rag.chunk_overlap,
            top_k=rag.top_k,
            use_rerank=rag.rerank,
        )


class RAGService:
    """Lazy facade over the full RAG pipeline."""

    def __init__(self, config: RAGConfig):
        self.config = config
        self._chunker: TreeSitterChunker | None = None
        self._embedder: Embedder | None = None
        self._store: ChromaStore | None = None
        self._indexer: Indexer | None = None
        self._retriever: Retriever | None = None
        self._initialised = False

    # -- lazy properties ---------------------------------------------------

    @property
    def chunker(self) -> TreeSitterChunker:
        self._ensure_init()
        assert self._chunker is not None
        return self._chunker

    @property
    def embedder(self) -> Embedder:
        self._ensure_init()
        assert self._embedder is not None
        return self._embedder

    @property
    def store(self) -> ChromaStore:
        self._ensure_init()
        assert self._store is not None
        return self._store

    @property
    def indexer(self) -> Indexer:
        self._ensure_init()
        assert self._indexer is not None
        return self._indexer

    @property
    def retriever(self) -> Retriever:
        self._ensure_init()
        assert self._retriever is not None
        return self._retriever

    # -- embedding backend selection (roadmap 18.3) ------------------------

    def _resolve_backend(self) -> str:
        """ollama | llama_cpp. Explicit config wins; auto probes GGUF + import."""
        configured = (self.config.embedding_backend or "").strip().lower()
        if configured in ("ollama", "llama_cpp", "llamacpp"):
            return "llama_cpp" if configured in ("llama_cpp", "llamacpp") else configured
        gguf = str(self.config.embedding_gguf or "").strip()
        if gguf and Path(gguf).is_file():
            try:
                import llama_cpp  # noqa: F401

                return "llama_cpp"
            except Exception:
                return "ollama"
        return "ollama"

    @property
    def embedding_backend_id(self) -> str:
        """Stable id stored/compared to detect stale indexes (never init-heavy)."""
        if self._embedder is not None:
            return getattr(self._embedder, "backend_id", "ollama:bge-m3")
        model = self.config.embedding_model or "bge-m3"
        return f"{'llama_cpp' if self._resolve_backend() == 'llama_cpp' else 'ollama'}:{model}"

    # -- lifecycle ---------------------------------------------------------

    def _ensure_init(self) -> None:
        if self._initialised:
            return
        self._chunker = TreeSitterChunker(
            max_chars=self.config.chunk_max_chars,
            overlap=self.config.chunk_overlap,
        )
        if self._resolve_backend() == "llama_cpp":
            from .embedder import LlamaCppEmbedder

            self._embedder = LlamaCppEmbedder.from_config(self.config)
        else:
            self._embedder = Embedder(
                host=self.config.ollama_host,
                model_name=self.config.embedding_model,
                device=self.config.embedding_device,
            )
        self._store = ChromaStore(persist_dir=self.config.chroma_dir)
        self._indexer = Indexer(self._chunker, self._embedder, self._store)
        self._retriever = Retriever(
            self._embedder, self._store,
            use_rerank=self.config.use_rerank,
            top_k=self.config.top_k,
        )
        self._initialised = True
        logger.info(
            "RAGService initialised (chroma_dir=%s, embeddings=%s)",
            self.config.chroma_dir,
            self.embedding_backend_id,
        )

    # -- public API --------------------------------------------------------

    def index(self, root: str | Path) -> int:
        """Index a directory tree. Returns chunk count."""
        self._ensure_init()
        return self._indexer.index_path(root)

    def index_file(self, file_path: str | Path) -> int:
        """Index a single file. Returns chunk count."""
        self._ensure_init()
        return self._indexer.index_file(file_path)

    def search(self, query: str, top_k: int | None = None) -> list[RetrievalResult]:
        """Retrieve relevant chunks for *query*."""
        self._ensure_init()
        return self._retriever.retrieve(query, top_k=top_k)

    def count(self) -> int:
        """Number of chunks currently stored."""
        self._ensure_init()
        return self._store.count()

    def clear(self) -> None:
        """Delete all indexed data."""
        self._ensure_init()
        self._indexer.clear()

    @property
    def is_ready(self) -> bool:
        """True when the index has at least one chunk."""
        self._ensure_init()
        return self._store.count() > 0

    # -- prompt augmentation ----------------------------------------------

    @staticmethod
    def build_context(results: list[RetrievalResult], max_chunks: int = 5) -> str:
        """Format retrieval results into a context string for the LLM."""
        if not results:
            return ""
        snippets: list[str] = []
        for i, r in enumerate(results[:max_chunks], 1):
            loc = r.file_path
            if r.name:
                loc = f"{loc}::{r.name}"
            loc += f" (L{r.start_line}-{r.end_line})"
            snippets.append(f"[{i}] {loc}\n{r.content}")
        header = (
            "Here are relevant code snippets from the indexed codebase. "
            "Use them to ground your answer:\n"
        )
        return header + "\n\n---\n\n".join(snippets) + "\n\n---\n"
