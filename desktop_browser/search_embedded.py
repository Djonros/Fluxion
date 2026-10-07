"""Tier 2 search provider: embedded incognito browser (roadmap 17.5.1).

Loads the DuckDuckGo lite page inside an off-record QWebEngineProfile
(no cookies/storage on disk) and extracts results from the live DOM.
QWebEngine must run on the GUI thread, so worker threads are marshalled
through a queued invocation; any failure degrades to the keyless
DuckDuckGoLiteProvider (tier 3) — never silence.
"""
from __future__ import annotations

import logging
import os
import threading

from PySide6.QtCore import Q_ARG, QEventLoop, QMetaObject, QObject, QTimer, Qt, Signal, Slot

from web.search_backend import (
    OptionalSearxngProvider,
    DuckDuckGoLiteProvider,
    SearchProvider,
    _clean_ddg_href,
    create_search_provider,
)
from web.searxng import SearchResult

logger = logging.getLogger(__name__)

_DDGLITE_LOAD_URL = "https://lite.duckduckgo.com/lite/?q={query}"

_EXTRACT_JS = """
(function() {
    const out = [];
    const links = Array.from(document.querySelectorAll('a.result-link'));
    const snips = Array.from(document.querySelectorAll('.result-snippet'));
    links.forEach(function(a, i) {
        out.push({
            title: (a.textContent || '').trim(),
            url: a.href || '',
            snippet: snips[i] ? (snips[i].textContent || '').trim() : ''
        });
    });
    return out;
})()
"""


def embedded_browser_available() -> bool:
    """QtWebEngine usable for search (not disabled, not offscreen/CI)."""
    if os.environ.get("FLUXION_DISABLE_WEBENGINE"):
        return False
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    try:
        from PySide6.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile  # noqa: F401
    except Exception:
        return False
    return True


class _EmbeddedSearchBridge(QObject):
    """Runs one search inside the GUI thread; publishes results via signal."""

    search_done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw: list | None = None
        self._evt = threading.Event()
        self.search_done.connect(self._collect)

    @Slot(object)
    def _collect(self, raw) -> None:
        self._raw = raw
        self._evt.set()

    @Slot(str)
    def search_sync(self, query: str) -> None:
        """GUI-thread slot: load DDG lite incognito, extract DOM, emit results."""
        from urllib.parse import quote_plus

        raw: list = []
        page = None
        profile = None
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtWebEngineWidgets import QWebEnginePage, QWebEngineProfile

            profile = QWebEngineProfile(self)  # off-record: no persistent storage
            page = QWebEnginePage(profile, self)
            loop = QEventLoop()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(loop.quit)
            box: dict = {"js": None, "done": False}

            def on_load(ok: bool) -> None:
                box["done"] = True
                if not ok:
                    loop.quit()
                    return
                page.runJavaScript(_EXTRACT_JS, lambda v: (box.__setitem__("js", v), loop.quit()))

            page.loadFinished.connect(on_load)
            timer.start(12000)
            page.load(QUrl(_DDGLITE_LOAD_URL.format(query=quote_plus(query))))
            loop.exec()
            value = box["js"]
            if isinstance(value, list):
                raw = [item for item in value if isinstance(item, dict)]
        except Exception as exc:
            logger.warning("Embedded browser search failed: %s", exc)
            raw = []
        finally:
            if page is not None:
                page.deleteLater()
            if profile is not None:
                profile.deleteLater()
        self.search_done.emit(raw)

    def search_from_worker(self, query: str, timeout: float = 20.0) -> list | None:
        """Called from non-GUI threads: marshal to the GUI thread and wait."""
        self._raw = None
        self._evt.clear()
        QMetaObject.invokeMethod(self, "search_sync", Qt.QueuedConnection, Q_ARG(str, query))
        if not self._evt.wait(timeout):
            return None
        return self._raw


class EmbeddedBrowserProvider(SearchProvider):
    """Tier 2: incognito Chromium search; degrades to tier 3 on any failure."""

    name = "embedded-browser"
    tier = "basic"

    def __init__(self, fallback: SearchProvider | None = None):
        self._fallback = fallback or DuckDuckGoLiteProvider()
        self._bridge: _EmbeddedSearchBridge | None = None

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        raw = None
        try:
            from PySide6.QtWidgets import QApplication
            from PySide6.QtCore import QThread

            app = QApplication.instance()
            if app is not None:
                if self._bridge is None:
                    self._bridge = _EmbeddedSearchBridge()
                    self._bridge.moveToThread(app.thread())
                if QThread.currentThread() is app.thread():
                    self._bridge.search_sync(query)
                else:
                    raw = self._bridge.search_from_worker(query)
        except Exception as exc:
            logger.warning("Embedded provider unavailable: %s", exc)
            raw = None

        results: list[SearchResult] = []
        for item in raw or []:
            url = _clean_ddg_href(str(item.get("url", "")))
            title = str(item.get("title", "")).strip()
            snippet = str(item.get("snippet", "")).strip()
            if url and title:
                results.append(SearchResult(title=title, url=url, snippet=snippet))
        if results:
            return results[:max_results]
        return self._fallback.search(query, max_results)


def create_desktop_search_provider(settings=None, use_searxng: bool = False) -> SearchProvider:
    """Desktop search: built-in (embedded incognito browser → DDG lite), plus
    SearXNG only when the user enabled it (``use_searxng``)."""
    from web.searxng import SearXNGClient

    timeout = getattr(settings, "timeout", 10)
    basic: SearchProvider = DuckDuckGoLiteProvider(timeout=timeout)
    if embedded_browser_available():
        basic = EmbeddedBrowserProvider(fallback=basic)
    client = SearXNGClient(
        base_url=getattr(settings, "searxng_url", None) or "http://localhost:8080",
        timeout=timeout,
    )
    return OptionalSearxngProvider(client, basic, enabled=use_searxng)
