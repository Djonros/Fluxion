"""Phase 4 tests: SearXNG client, cache, fetcher, pipeline.

SearXNG integration tests are skipped when the server is not running.
All other tests (cache, mock pipeline, fetcher extraction) run offline.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from web import (
    ContentFetcher,
    FetchedPage,
    SearXNGClient,
    SearXNGError,
    SearchResult,
    WebCache,
    WebContext,
    WebSearch,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  WebCache
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebCache:
    def test_set_and_get(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"), default_ttl=3600)
        cache.set("https://example.com", "Example", "Hello world")
        result = cache.get("https://example.com")
        assert result is not None
        title, content = result
        assert title == "Example"
        assert content == "Hello world"

    def test_get_missing_returns_none(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"))
        assert cache.get("https://nope.com") is None

    def test_expired_entry_returns_none(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"), default_ttl=0)
        cache.set("https://old.com", "Old", "Stale data")
        import time
        time.sleep(0.1)
        assert cache.get("https://old.com") is None

    def test_overwrite_on_set(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"))
        cache.set("https://x.com", "V1", "Content1")
        cache.set("https://x.com", "V2", "Content2")
        title, content = cache.get("https://x.com")
        assert title == "V2"
        assert content == "Content2"

    def test_count(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"))
        cache.set("https://a.com", "A", "A")
        cache.set("https://b.com", "B", "B")
        assert cache.count() == 2

    def test_clear(self, tmp_path):
        cache = WebCache(db_path=str(tmp_path / "cache.db"))
        cache.set("https://a.com", "A", "A")
        cache.clear()
        assert cache.count() == 0


# ═══════════════════════════════════════════════════════════════════════════════
#  SearXNGClient (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearXNGClient:
    def test_search_parses_results(self):
        mock_response = {
            "results": [
                {
                    "title": "FastAPI Docs",
                    "url": "https://fastapi.tiangolo.com",
                    "content": "FastAPI is a modern web framework",
                    "engines": ["duckduckgo"],
                    "score": 5.0,
                },
                {
                    "title": "GitHub",
                    "url": "https://github.com/tiangolo/fastapi",
                    "content": "The source code",
                    "engines": ["google"],
                    "score": 3.0,
                },
            ]
        }
        client = SearXNGClient(base_url="http://fake:8080", timeout=5)
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = client.search("fastapi")

        assert len(results) == 2
        assert results[0].title == "FastAPI Docs"
        assert results[0].url == "https://fastapi.tiangolo.com"
        assert "duckduckgo" in results[0].engines

    def test_search_raises_on_connection_error(self):
        client = SearXNGClient(base_url="http://fake:8080", timeout=1)
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(SearXNGError):
                client.search("test")

    def test_search_limits_results(self):
        mock_response = {
            "results": [
                {"title": f"Result {i}", "url": f"https://r{i}.com", "content": "x", "engines": []}
                for i in range(20)
            ]
        }
        client = SearXNGClient(base_url="http://fake:8080")
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            results = client.search("test", max_results=5)
        assert len(results) == 5

    def test_is_alive_false_on_error(self):
        client = SearXNGClient(base_url="http://fake:99999")
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            assert client.is_alive() is False


# ═══════════════════════════════════════════════════════════════════════════════
#  ContentFetcher (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestContentFetcher:
    def test_fetch_extracts_content(self):
        html = """
        <html><head><title>Test Page</title></head>
        <body><main><p>This is the main content of the page.</p></main></body></html>
        """
        fetcher = ContentFetcher(timeout=5)
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            page = fetcher.fetch("https://example.com/page")

        assert page.success
        assert "main content" in page.content.lower() or len(page.content) > 0

    def test_fetch_network_error(self):
        fetcher = ContentFetcher(timeout=1)
        with patch("httpx.get", side_effect=httpx.ConnectError("refused")):
            page = fetcher.fetch("https://nope.com")
        assert not page.success
        assert page.error

    def test_fetch_truncates_long_content(self):
        long_html = "<html><body>" + ("<p>content</p>" * 5000) + "</body></html>"
        fetcher = ContentFetcher(timeout=5, max_chars=500)
        with patch("httpx.get") as mock_get:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.text = long_html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp
            page = fetcher.fetch("https://long.com")
        if page.success:
            assert len(page.content) <= 503  # max_chars + "..."


# ═══════════════════════════════════════════════════════════════════════════════
#  WebSearch pipeline (mocked)
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebSearch:
    def _make_pipeline(self, tmp_path):
        client = SearXNGClient(base_url="http://fake:8080")
        fetcher = ContentFetcher(timeout=5)
        cache = WebCache(db_path=str(tmp_path / "cache.db"), default_ttl=3600)
        return WebSearch(client=client, fetcher=fetcher, cache=cache, max_pages=2)

    def test_run_returns_contexts(self, tmp_path):
        ws = self._make_pipeline(tmp_path)
        ws.client.search = MagicMock(return_value=[
            SearchResult(title="FastAPI", url="https://fastapi.tiangolo.com", snippet="Web framework"),
            SearchResult(title="Docs", url="https://fastapi.tiangolo.com/docs", snippet="Documentation"),
        ])
        ws.fetcher.fetch = MagicMock(return_value=FetchedPage(
            url="https://fastapi.tiangolo.com",
            title="FastAPI",
            content="FastAPI is a modern framework.",
            success=True,
        ))

        results = ws.run("fastapi")

        assert len(results) == 2
        assert results[0].title == "FastAPI"
        assert "framework" in results[0].content.lower()

    def test_run_uses_cache_on_second_call(self, tmp_path):
        ws = self._make_pipeline(tmp_path)
        ws.client.search = MagicMock(return_value=[
            SearchResult(title="Test", url="https://test.com", snippet="Snip"),
        ])
        ws.fetcher.fetch = MagicMock(return_value=FetchedPage(
            url="https://test.com", title="Test", content="Cached content", success=True,
        ))

        ws.run("test")
        ws.run("test")

        # fetcher should only be called once (second run uses cache)
        assert ws.fetcher.fetch.call_count == 1

    def test_run_skips_failed_fetches(self, tmp_path):
        ws = self._make_pipeline(tmp_path)
        ws.client.search = MagicMock(return_value=[
            SearchResult(title="Good", url="https://good.com", snippet="OK"),
            SearchResult(title="Bad", url="https://bad.com", snippet=""),
            SearchResult(title="Also Good", url="https://good2.com", snippet="OK2"),
        ])
        ws.fetcher.fetch = MagicMock(side_effect=[
            FetchedPage(url="https://good.com", title="Good", content="Content1", success=True),
            FetchedPage(url="https://bad.com", title="", content="", success=False, error="timeout"),
            FetchedPage(url="https://good2.com", title="Good2", content="Content2", success=True),
        ])

        results = ws.run("test", max_pages=3)
        assert len(results) == 3
        assert results[1].content == ""  # failed fetch still returns context with snippet

    def test_run_handles_search_error(self, tmp_path):
        ws = self._make_pipeline(tmp_path)
        ws.client.search = MagicMock(side_effect=SearXNGError("server down"))

        class _DeadFallback:
            name = "duckduckgo-lite"
            tier = "basic"

            def search(self, query, max_results=10):
                return []

        ws._fallback = _DeadFallback()
        results = ws.run("test")
        assert results == []

    def test_build_context_formats_citations(self):
        contexts = [
            WebContext(title="A", url="https://a.com", content="Content A", citation_index=1),
            WebContext(title="B", url="https://b.com", content="Content B", citation_index=2),
        ]
        result = WebSearch.build_context(contexts)
        assert "[1]" in result
        assert "[2]" in result
        assert "Content A" in result
        assert "https://a.com" in result

    def test_build_context_empty(self):
        assert WebSearch.build_context([]) == ""

    def test_build_context_respects_max_chars(self):
        contexts = [
            WebContext(title=f"Page {i}", url=f"https://p{i}.com", content="X" * 3000, citation_index=i + 1)
            for i in range(5)
        ]
        result = WebSearch.build_context(contexts, max_chars=2000)
        assert len(result) <= 2500  # some slack for headers


# ═══════════════════════════════════════════════════════════════════════════════
#  SearXNG integration (requires running server)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSearXNGIntegration:
    @pytest.fixture()
    def client(self):
        c = SearXNGClient(base_url="http://localhost:8080", timeout=10)
        if not c.is_alive():
            pytest.skip("SearXNG not running on localhost:8080")
        return c

    def test_live_search_returns_results(self, client: SearXNGClient):
        results = client.search("python programming", max_results=3)
        assert len(results) > 0
        assert all(r.url for r in results)
