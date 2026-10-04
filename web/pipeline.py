"""WebSearch pipeline: search → fetch → clean → cache → WebContext[]."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .cache import WebCache
from .fetcher import ContentFetcher
from .search_backend import (
    DuckDuckGoLiteProvider,
    SearchProvider,
    SearxngProvider,
    create_search_provider,
)
from .searxng import SearXNGClient, SearchResult

if TYPE_CHECKING:
    from core.config import WebSettings

logger = logging.getLogger(__name__)


@dataclass
class WebContext:
    """A single web result ready for injection into the prompt."""
    title: str
    url: str
    snippet: str = ""
    content: str = ""
    citation_index: int = 0


class WebSearch:
    """Orchestrates SearXNG search, page fetching, and caching."""

    def __init__(
        self,
        client: SearXNGClient,
        fetcher: ContentFetcher,
        cache: WebCache,
        max_pages: int = 3,
        provider: SearchProvider | None = None,
    ):
        self.client = client
        self.fetcher = fetcher
        self.cache = cache
        self.max_pages = max_pages
        self.provider: SearchProvider = provider or SearxngProvider(client)
        self._fallback = DuckDuckGoLiteProvider()

    @classmethod
    def from_settings(cls, settings: "WebSettings", provider: SearchProvider | None = None) -> "WebSearch":
        return cls(
            client=SearXNGClient(
                base_url=settings.searxng_url,
                timeout=settings.timeout,
            ),
            fetcher=ContentFetcher(timeout=settings.timeout),
            cache=WebCache(default_ttl=settings.cache_ttl_hours * 3600),
            max_pages=settings.max_pages,
            provider=provider if provider is not None else create_search_provider(settings),
        )

    @property
    def provider_name(self) -> str:
        return getattr(self.provider, "name", "searxng")

    @property
    def provider_tier(self) -> str:
        return getattr(self.provider, "tier", "enhanced")

    @property
    def brief_mode(self) -> bool:
        """True when the fetcher degrades to snippet-only extracts (Lite)."""
        return bool(getattr(self.fetcher, "brief_mode", False))

    def run(self, query: str, max_pages: int | None = None) -> list[WebContext]:
        """Execute the full pipeline: search → fetch → cache → return contexts."""
        limit = max_pages if max_pages is not None else self.max_pages

        try:
            results = self.provider.search(query, max_results=limit * 3)
        except Exception as exc:
            logger.warning("Search via %s failed: %s", self.provider_name, exc)
            results = []
        # A provider that switches between tiers itself (desktop: optional
        # SearXNG) must not be replaced — that would undo the user's choice.
        if (
            not results
            and self.provider.tier == "enhanced"
            and getattr(self.provider, "manages_fallback", False) is not True
        ):
            # Golden Rule of Silence: degrade to the keyless tier, never fail quietly.
            try:
                results = self._fallback.search(query, max_results=limit * 3)
                if results:
                    self.provider = self._fallback
                    logger.info("Search degraded to %s", self.provider_name)
            except Exception as exc:
                logger.warning("Fallback search failed: %s", exc)

        contexts: list[WebContext] = []
        for i, sr in enumerate(results):
            if len(contexts) >= limit:
                break
            ctx = self._process_result(sr, i + 1)
            if ctx:
                contexts.append(ctx)

        return contexts

    def _process_result(self, sr: SearchResult, index: int) -> WebContext | None:
        if not sr.url:
            return None

        cached = self.cache.get(sr.url)
        if cached:
            title, content = cached
            return WebContext(
                title=title or sr.title,
                url=sr.url,
                snippet=sr.snippet,
                content=content,
                citation_index=index,
            )

        page = self.fetcher.fetch(sr.url)
        if not page.success or not page.content.strip():
            logger.debug("Fetch failed for %s: %s", sr.url, page.error)
            return WebContext(
                title=sr.title,
                url=sr.url,
                snippet=sr.snippet,
                content="",
                citation_index=index,
            )

        self.cache.set(sr.url, page.title or sr.title, page.content)
        return WebContext(
            title=page.title or sr.title,
            url=sr.url,
            snippet=sr.snippet,
            content=page.content,
            citation_index=index,
        )

    @staticmethod
    def build_context(contexts: list[WebContext], max_chars: int = 6000) -> str:
        """Format web results into a context block for the LLM prompt."""
        if not contexts:
            return ""

        parts: list[str] = []
        total = 0
        for ctx in contexts:
            tag = f"[{ctx.citation_index}]"
            header = f"{tag} {ctx.title}\n{ctx.url}\n"
            body = ctx.content if ctx.content else ctx.snippet
            if not body:
                continue
            chunk = header + body.strip() + "\n\n---\n"
            if total + len(chunk) > max_chars:
                remaining = max_chars - total - len(header) - 10
                if remaining > 100:
                    chunk = header + body.strip()[:remaining] + "...\n\n---\n"
                else:
                    break
            parts.append(chunk)
            total += len(chunk)

        return (
            "Here are relevant web search results. "
            "Reference them as [1], [2], etc.\n\n"
            + "".join(parts)
        )
