"""Retriever: query ChromaDB with optional flashrank reranking."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from .embedder import Embedder
from .store import ChromaStore

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    content: str
    file_path: str
    start_line: int
    end_line: int
    node_type: str
    name: str
    score: float  # cosine similarity (1 - distance), higher is better
    rank: int = 0


class Retriever:
    def __init__(self, embedder: Embedder, store: ChromaStore, use_rerank: bool = True, top_k: int = 8):
        self.embedder = embedder
        self.store = store
        self.use_rerank = use_rerank
        self.top_k = top_k
        self._reranker = None

    def retrieve(self, query: str, top_k: int | None = None) -> list[RetrievalResult]:
        k = top_k or self.top_k
        q_embedding = self.embedder.embed_query(query)
        raw = self.store.query(q_embedding, n_results=k * 2 if self.use_rerank else k)
        results = self._parse(raw)

        if self.use_rerank and len(results) > 1:
            results = self._rerank(query, results)
        return results[:k]

    def _parse(self, raw: dict) -> list[RetrievalResult]:
        documents = raw.get("documents", [[]])
        metadatas = raw.get("metadatas", [[]])
        distances = raw.get("distances", [[]])

        if not documents or not documents[0]:
            return []

        results: list[RetrievalResult] = []
        for i, (doc, meta, dist) in enumerate(
            zip(documents[0], metadatas[0], distances[0])
        ):
            score = max(0.0, 1.0 - dist)
            results.append(
                RetrievalResult(
                    content=doc,
                    file_path=meta.get("file_path", ""),
                    start_line=meta.get("start_line", 0),
                    end_line=meta.get("end_line", 0),
                    node_type=meta.get("node_type", ""),
                    name=meta.get("name", ""),
                    score=score,
                    rank=i,
                )
            )
        return results

    def _rerank(self, query: str, results: list[RetrievalResult]) -> list[RetrievalResult]:
        try:
            if self._reranker is None:
                from flashrank import Ranker

                self._reranker = Ranker()
        except Exception:
            logger.debug("flashrank not available, skipping rerank")
            return results

        from flashrank import RerankRequest

        passages = [
            {"id": i, "text": r.content}
            for i, r in enumerate(results)
        ]
        rerank_req = RerankRequest(query=query, passages=passages)
        ranked = self._reranker.rerank(rerank_req)

        id_map = {r["id"]: r["score"] for r in ranked}
        for i, r in enumerate(results):
            r.score = float(id_map.get(i, r.score))
        results.sort(key=lambda x: x.score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i
        return results
