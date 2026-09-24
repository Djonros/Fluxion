"""Tiered search providers (roadmap 17.5): SearXNG → keyless DuckDuckGo lite.

Tier 1 (enhanced): local SearXNG instance — multi-engine aggregation.
Tier 3 (basic):    direct keyless scrape of html.duckduckgo.com/lite.

Tier 2 (embedded incognito browser) lives in the desktop layer
(desktop_browser.search_embedded) because it needs QtWebEngine on the
GUI thread; the sync pipeline here degrades 1 → 3.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from .searxng import SearXNGClient, SearchResult

logger = logging.getLogger(__name__)

_DDGLITE_URL = "https://html.duckduckgo.com/lite/"
_DDGLITE_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class SearchProvider:
    """Minimal sync contract used by WebSearch."""

    name = "base"
    tier = "basic"  # "enhanced" | "basic"

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        raise NotImplementedError

    def is_alive(self) -> bool:
        return True


class SearxngProvider(SearchProvider):
    """Tier 1: wraps the existing SearXNG JSON client."""

    name = "searxng"
    tier = "enhanced"

    def __init__(self, client: SearXNGClient):
        self.client = client

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        return self.client.search(query, max_results=max_results)

    def is_alive(self) -> bool:
        return bool(self.client.is_alive())


class DuckDuckGoLiteProvider(SearchProvider):
    """Tier 3: keyless scrape of the DuckDuckGo lite HTML UI."""

    name = "duckduckgo-lite"
    tier = "basic"

    def __init__(self, timeout: int = 10):
        self.timeout = timeout

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        try:
            resp = httpx.get(
                _DDGLITE_URL,
                params={"q": query},
                headers={"User-Agent": _DDGLITE_UA},
                timeout=self.timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning("DuckDuckGo lite search failed: %s", exc)
            return []
        return parse_ddg_lite(resp.text)[:max_results]


def _clean_ddg_href(href: str) -> str:
    """Unwrap DuckDuckGo redirect links; drop ad links."""
    href = unescape(href or "")
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href:
        target = parse_qs(urlparse(href).query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    if "//duckduckgo.com/y.js" in href or "duckduckgo.com/y.js" in href:
        return ""
    return href


def _strip_tags(fragment: str) -> str:
    text = unescape(fragment or "")
    return re.sub(r"<[^>]+>", "", text).strip()


def parse_ddg_lite(html_text: str) -> list[SearchResult]:
    """Parse html.duckduckgo.com/lite markup into SearchResult list.

    Links carry class ``result-link``; snippets live in the following
    ``result-snippet`` cell, so we zip them by document order.
    """
    links: list[tuple[str, str]] = []
    for m in re.finditer(r"<a([^>]*)>(.*?)</a>", html_text, re.DOTALL):
        attrs, inner = m.group(1), m.group(2)
        if "result-link" not in attrs:
            continue
        href_m = re.search(r"href=[\"']([^\"']+)[\"']", attrs)
        if href_m is None:
            continue
        links.append((href_m.group(1), inner))

    snippets = [
        _strip_tags(m.group(1))
        for m in re.finditer(
            r"class=[\"']result-snippet[\"'][^>]*>(.*?)</td>", html_text, re.DOTALL
        )
    ]

    results: list[SearchResult] = []
    for i, (href, title_html) in enumerate(links):
        url = _clean_ddg_href(href)
        title = _strip_tags(title_html)
        snippet = snippets[i] if i < len(snippets) else ""
        if url and title:
            results.append(SearchResult(title=title, url=url, snippet=snippet))
    return results


def create_search_provider(settings=None) -> SearchProvider:
    """Pick tier 1 when a local SearXNG answers; otherwise tier 3."""
    base_url = getattr(settings, "searxng_url", None) or "http://localhost:8080"
    timeout = getattr(settings, "timeout", 10)
    client = SearXNGClient(base_url=base_url, timeout=timeout)
    try:
        if client.is_alive():
            return SearxngProvider(client)
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("SearXNG probe failed: %s", exc)
    return DuckDuckGoLiteProvider(timeout=timeout)
