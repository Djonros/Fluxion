"""SearXNG client: queries a local SearXNG instance with JSON output."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_URL = "http://localhost:8080"
_DEFAULT_TIMEOUT = 10


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    engines: list[str] = field(default_factory=list)
    score: float = 0.0


class SearXNGError(Exception):
    pass


class SearXNGClient:
    """Thin client over the SearXNG JSON search API."""

    def __init__(
        self,
        base_url: str = _DEFAULT_URL,
        timeout: int = _DEFAULT_TIMEOUT,
        max_results: int = 10,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_results = max_results

    def search(self, query: str, categories: str = "general", max_results: int | None = None) -> list[SearchResult]:
        """Execute a search and return parsed results."""
        limit = max_results or self.max_results
        params = {
            "q": query,
            "format": "json",
            "categories": categories,
        }
        try:
            resp = httpx.get(
                f"{self.base_url}/search",
                params=params,
                timeout=self.timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SearXNGError(f"SearXNG returned {exc.response.status_code}") from exc
        except httpx.RequestError as exc:
            raise SearXNGError(f"Cannot reach SearXNG at {self.base_url}: {exc}") from exc

        data = resp.json()
        raw_results = data.get("results", [])
        results: list[SearchResult] = []
        for item in raw_results[:limit]:
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("content", ""),
                    engines=item.get("engines", []),
                    score=float(item.get("score", 0.0)),
                )
            )
        return results

    def is_alive(self) -> bool:
        """Quick health check."""
        try:
            resp = httpx.get(f"{self.base_url}/healthz", timeout=5)
            return resp.status_code == 200
        except Exception:
            try:
                resp = httpx.get(self.base_url, timeout=5)
                return resp.status_code in (200, 302)
            except Exception:
                return False
