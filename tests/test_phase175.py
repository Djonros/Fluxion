"""Phase 17.5: docker-free search (tiered providers, notices, optional deps)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from desktop_browser.app import FluxionWindow
from web.pipeline import WebSearch
from web.search_backend import (
    DuckDuckGoLiteProvider,
    SearxngProvider,
    create_search_provider,
    parse_ddg_lite,
)
from web.searxng import SearchResult


@pytest.fixture(scope="module", autouse=True)
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def make_window(tmp_path, monkeypatch):
    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")

    def factory(**kwargs):
        ini = tmp_path / f"settings_{len(list(tmp_path.iterdir()))}.ini"
        return FluxionWindow(qsettings=QSettings(str(ini), QSettings.IniFormat), **kwargs)

    return factory


_DDGLITE_FIXTURE = """
<html><body><table>
<tr><td><a rel="nofollow" href="https://example.com/alpha" class='result-link'>Alpha &amp; Beta</a></td></tr>
<tr><td class='result-snippet'>First snippet &lt;b&gt;bold&lt;/b&gt;</td></tr>
<tr><td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbeta&amp;rut=abc" class='result-link'>Beta page</a></td></tr>
<tr><td class='result-snippet'>Second snippet</td></tr>
<tr><td><a rel="nofollow" href="http://duckduckgo.com/y.js?ad=1" class='result-link'>Ad</a></td></tr>
</table></body></html>
"""


def test_parse_ddg_lite_fixture():
    results = parse_ddg_lite(_DDGLITE_FIXTURE)
    assert len(results) == 2
    assert results[0].title == "Alpha & Beta"
    assert results[0].url == "https://example.com/alpha"
    assert results[0].snippet == "First snippet bold"
    assert results[1].url == "https://example.org/beta"
    assert results[1].snippet == "Second snippet"


def test_create_search_provider_prefers_searxng(monkeypatch):
    import web.search_backend as sb

    class FakeClient:
        def __init__(self, base_url, timeout):
            self.base_url = base_url

        def is_alive(self):
            return True

    monkeypatch.setattr(sb, "SearXNGClient", FakeClient)
    provider = create_search_provider(SimpleNamespace(searxng_url="http://x", timeout=1))
    assert isinstance(provider, SearxngProvider)


def test_create_search_provider_falls_back_to_ddg(monkeypatch):
    import web.search_backend as sb

    class DeadClient:
        def __init__(self, base_url, timeout):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(sb, "SearXNGClient", DeadClient)
    provider = create_search_provider(SimpleNamespace(searxng_url="http://x", timeout=1))
    assert isinstance(provider, DuckDuckGoLiteProvider)


class _RaisingProvider:
    name = "searxng"
    tier = "enhanced"

    def search(self, query, max_results=10):
        raise RuntimeError("searxng down")


class _FakeFallback:
    name = "duckduckgo-lite"
    tier = "basic"

    def search(self, query, max_results=10):
        return [SearchResult(title="T", url="https://example.com/a", snippet="s")]


def test_websearch_degrades_to_fallback():
    fetcher = SimpleNamespace(
        fetch=lambda url: SimpleNamespace(success=True, content="body", title="T", error="")
    )
    cache = SimpleNamespace(get=lambda url: None, set=lambda *a: None)
    ws = WebSearch(client=object(), fetcher=fetcher, cache=cache, provider=_RaisingProvider())
    ws._fallback = _FakeFallback()
    contexts = ws.run("query")
    assert contexts and contexts[0].url == "https://example.com/a"
    assert ws.provider_tier == "basic"


def test_check_docker_is_optional(monkeypatch):
    import desktop_browser.health as health

    monkeypatch.setattr(health.shutil, "which", lambda name: None)
    status = health.check_docker()
    assert status.available is True
    assert "необяз" in status.remedy.lower() or "опционально" in status.title.lower()


def test_check_searxng_basic_tier_is_ok():
    import desktop_browser.health as health

    web_search = SimpleNamespace(
        provider_tier="basic",
        client=SimpleNamespace(base_url="http://localhost:8080"),
    )
    status = health.check_searxng(web_search)
    assert status.available is True
    assert "базовый" in status.title.lower()


def test_meta_shows_search_tier(make_window):
    window = make_window(web_search=SimpleNamespace(brief_mode=False, provider_tier="basic"))
    window._messages.append({"role": "assistant", "text": "", "meta": ""})
    window._on_meta(SimpleNamespace(web_used=True, web_sources=["a"]))
    assert "поиск: базовый (один движок)" in window._messages[-1]["meta"]


# ── 17.5.1 tier 2: embedded incognito browser ────────────────────────────


def test_embedded_available_flag_respects_env(monkeypatch):
    from desktop_browser.search_embedded import embedded_browser_available

    monkeypatch.setenv("FLUXION_DISABLE_WEBENGINE", "1")
    assert embedded_browser_available() is False
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.delenv("FLUXION_DISABLE_WEBENGINE", raising=False)
    assert embedded_browser_available() is False


def test_embedded_provider_falls_back_to_ddg(monkeypatch):
    from desktop_browser import search_embedded as se

    class _FakeFallback:
        name = "duckduckgo-lite"
        tier = "basic"

        def search(self, query, max_results=10):
            return [SearchResult(title="FB", url="https://fb.example", snippet="")]

    def boom(self, query):
        raise RuntimeError("no webengine")

    monkeypatch.setattr(se._EmbeddedSearchBridge, "search_sync", boom)
    provider = se.EmbeddedBrowserProvider(fallback=_FakeFallback())
    results = provider.search("q")
    assert len(results) == 1
    assert results[0].url == "https://fb.example"


def test_create_desktop_search_provider_skips_embedded_offscreen(monkeypatch):
    import web.search_backend as sb
    from desktop_browser.search_embedded import create_desktop_search_provider

    class DeadClient:
        def __init__(self, base_url, timeout):
            pass

        def is_alive(self):
            return False

    monkeypatch.setattr(sb, "SearXNGClient", DeadClient)
    monkeypatch.setattr(
        "desktop_browser.search_embedded.embedded_browser_available", lambda: False
    )
    provider = create_desktop_search_provider(SimpleNamespace(searxng_url="http://x", timeout=1))
    assert isinstance(provider, DuckDuckGoLiteProvider)
