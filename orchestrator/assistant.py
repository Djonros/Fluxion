"""Assistant: single entry point that routes queries and assembles context."""
from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from core.inference import ModelBackend
from core.config import GenerationSettings, Settings
from .prompt_builder import PromptBuilder, PromptContext, SYSTEM_BASE
from .router import Router
from .strategy import QuerySignals, Strategy

if TYPE_CHECKING:
    from rag.service import RAGService
    from web.pipeline import WebSearch

logger = logging.getLogger(__name__)

RAG_FALLBACK_THRESHOLD = 0.3


@dataclass
class AskResult:
    """Metadata returned alongside streaming tokens."""
    strategy: Strategy
    signals: QuerySignals
    rag_used: bool = False
    rag_sources: list[str] = field(default_factory=list)
    web_used: bool = False
    web_sources: list[str] = field(default_factory=list)
    messages: list[dict[str, str]] = field(default_factory=list)


class Assistant:
    """Facade connecting Router → RAG → Web → PromptBuilder → Backend."""

    def __init__(
        self,
        backend: ModelBackend,
        settings: Settings,
        rag_service: "RAGService | None" = None,
        web_search: "WebSearch | None" = None,
        prompt_builder: PromptBuilder | None = None,
    ):
        self.backend = backend
        self.settings = settings
        self.rag_service = rag_service
        self.web_search = web_search
        self.prompt_builder = prompt_builder or PromptBuilder(
            num_ctx=settings.generation.num_ctx,
            max_predict=settings.generation.max_tokens,
        )
        self.router = Router()
        self.history: list[dict[str, str]] = []
        self._last_rag_results: list = []
        self._last_web_contexts: list = []

    @property
    def web_available(self) -> bool:
        return self.web_search is not None

    # ── public API ─────────────────────────────────────────────────────────

    def ask(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        gen: GenerationSettings | None = None,
        force_strategy: Strategy | None = None,
    ) -> Iterator[str]:
        """Stream a response for *query*. Yields token chunks."""
        result, token_stream = self.ask_stream(query, history, gen, force_strategy)
        yield from token_stream

    def ask_stream(
        self,
        query: str,
        history: list[dict[str, str]] | None = None,
        gen: GenerationSettings | None = None,
        force_strategy: Strategy | None = None,
    ) -> tuple[AskResult, Iterator[str]]:
        """Like ask() but also returns AskResult metadata before streaming."""
        result = self._prepare(query, force_strategy)
        result.messages = self._build_messages(query, result, history)
        return result, self.backend.stream(result.messages, gen=gen)

    def classify(self, query: str) -> QuerySignals:
        """Return routing signals without making a backend call."""
        return self.router.classify(query)

    def reset(self) -> None:
        self.history.clear()

    # ── internals ─────────────────────────────────────────────────────────

    def _prepare(self, query: str, force_strategy: Strategy | None = None) -> AskResult:
        signals = self.router.classify(query)
        if force_strategy:
            signals.strategy = force_strategy
        result = AskResult(strategy=signals.strategy, signals=signals)

        strategy = result.strategy

        # ── RAG ──
        if strategy in (Strategy.RAG, Strategy.RAG_THEN_WEB) and self.rag_service:
            self._do_rag(query, result)

        # ── WEB (always, or fallback from weak RAG) ──
        if strategy == Strategy.WEB and self.web_search:
            self._do_web(query, result)
        elif strategy == Strategy.RAG_THEN_WEB:
            rag_top_score = self._rag_top_score()
            if rag_top_score < RAG_FALLBACK_THRESHOLD and self.web_search:
                self._do_web(query, result)
            elif not result.rag_used and self.web_search:
                self._do_web(query, result)

        # ── Force web if web forced ──
        if force_strategy == Strategy.WEB and self.web_search and not result.web_used:
            self._do_web(query, result)

        return result

    def _do_rag(self, query: str, result: AskResult) -> None:
        try:
            results = self.rag_service.search(query, top_k=5)
        except Exception:
            logger.warning("RAG retrieval failed", exc_info=True)
            results = []
        if results:
            result.rag_used = True
            result.rag_sources = [
                f"{r.file_path}::{r.name or r.node_type}" for r in results
            ]
        self._last_rag_results = results

    def _do_web(self, query: str, result: AskResult) -> None:
        try:
            contexts = self.web_search.run(query)
        except Exception:
            logger.warning("Web search failed", exc_info=True)
            contexts = []
        if contexts:
            result.web_used = True
            result.web_sources = [c.url for c in contexts]
        self._last_web_contexts = contexts

    def _rag_top_score(self) -> float:
        if not self._last_rag_results:
            return 0.0
        return max(r.score for r in self._last_rag_results)

    def _build_messages(
        self,
        query: str,
        result: AskResult,
        history: list[dict[str, str]] | None,
    ) -> list[dict[str, str]]:
        rag_snippets: list[tuple[str, str]] = []
        web_snippets: list[tuple[str, str]] = []

        if result.rag_used and self._last_rag_results:
            from rag.service import RAGService
            ctx_text = RAGService.build_context(self._last_rag_results)
            rag_snippets = [("rag", ctx_text)]

        if result.web_used and self._last_web_contexts:
            from web.pipeline import WebSearch as WS
            ctx_text = WS.build_context(self._last_web_contexts)
            web_snippets = [("web", ctx_text)]

        return self.prompt_builder.build(
            PromptContext(
                rag_snippets=rag_snippets,
                web_snippets=web_snippets,
                history=history or self.history,
                query=query,
            )
        )
