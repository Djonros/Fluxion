"""ChromaDB vector store: add/query/delete with persistence."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_COLLECTION = "codebase"


class ChromaStore:
    """Persistent ChromaDB wrapper with cosine distance."""

    def __init__(self, persist_dir: str = "data/chroma", collection_name: str = _DEFAULT_COLLECTION):
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.collection_name = collection_name
        self._client = None
        self._collection = None

    def _ensure_connected(self):
        if self._client is not None:
            return
        import chromadb

        self._client = chromadb.PersistentClient(path=str(self.persist_dir))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def add(
        self,
        ids: list[str],
        documents: list[str],
        embeddings: np.ndarray,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        self._ensure_connected()
        assert self._collection is not None
        self._collection.upsert(
            ids=ids,
            documents=documents,
            embeddings=embeddings.tolist(),
            metadatas=metadatas,
        )

    def query(
        self,
        query_embedding: np.ndarray,
        n_results: int = 8,
    ) -> dict[str, Any]:
        self._ensure_connected()
        assert self._collection is not None
        return self._collection.query(
            query_embeddings=query_embedding.tolist(),
            n_results=n_results,
            include=["documents", "metadatas", "distances"],
        )

    def delete_all(self) -> None:
        self._ensure_connected()
        assert self._client is not None
        try:
            self._client.delete_collection(self.collection_name)
        except Exception:
            pass
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )

    def count(self) -> int:
        self._ensure_connected()
        assert self._collection is not None
        return self._collection.count()

    def get_all_sources(self) -> set[str]:
        self._ensure_connected()
        assert self._collection is not None
        result = self._collection.get(include=["metadatas"])
        sources: set[str] = set()
        for meta in result.get("metadatas", []):
            if meta and "file_path" in meta:
                sources.add(meta["file_path"])
        return sources
