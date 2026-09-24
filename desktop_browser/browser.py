"""Embedded browser panel for the Fluxion Browser build.

Provides a real QWebEngineView-based panel when QtWebEngine is available,
and a safe widget-based fallback (offscreen CI, missing engine, disabled
via FLUXION_DISABLE_WEBENGINE=1).
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import quote_plus

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

try:  # pragma: no cover - exercised implicitly via WEBENGINE_AVAILABLE
    from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

    WEBENGINE_AVAILABLE = True
except Exception:
    WEBENGINE_AVAILABLE = False

_DUCKDUCKGO = "https://duckduckgo.com/?q="
_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def webengine_enabled() -> bool:
    """True when the real engine may be created in this session."""
    if os.environ.get("FLUXION_DISABLE_WEBENGINE") == "1":
        return False
    if os.environ.get("QT_QPA_PLATFORM") in ("offscreen", "minimal"):
        return False
    return WEBENGINE_AVAILABLE


def webengine_dark_enabled() -> bool:
    """True when embedded pages should render dark (FLUXION_WEBENGINE_DARK!=0)."""
    return os.environ.get("FLUXION_WEBENGINE_DARK", "1") != "0"


def _apply_dark_chromium_flags() -> None:
    """Ask Chromium to render pages dark; must run before the engine starts."""
    if not webengine_dark_enabled():
        return
    flags = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    if "--force-dark-mode" not in flags.split():
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            (flags + " " if flags else "") + "--force-dark-mode"
        )


def looks_like_url(text: str) -> bool:
    candidate = text.strip()
    return bool(candidate) and " " not in candidate and "." in candidate


def normalize_url(url: str) -> str:
    candidate = url.strip()
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", candidate):
        candidate = "https://" + candidate
    return candidate


def linkify(text: str) -> str:
    """Escape plain text and turn http(s) URLs into anchors."""
    parts: list[str] = []
    pos = 0
    for match in _URL_RE.finditer(text):
        parts.append(html.escape(text[pos : match.start()]))
        url = match.group(0).rstrip('.,);]')
        parts.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(url)}</a>')
        pos = match.start() + len(url)
    parts.append(html.escape(text[pos:]))
    return "".join(parts)


class BrowserPanel(QWidget):
    """Real embedded browser backed by QWebEngineView."""

    def __init__(self, search_base: str = "", parent=None):
        super().__init__(parent)
        self.search_base = (search_base or "").rstrip("/")
        self.setObjectName("webPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        self.address = QLineEdit()
        self.address.setObjectName("webAddress")
        self.address.setPlaceholderText("URL или поисковый запрос, затем Enter")
        self.address.setClearButtonEnabled(True)
        self.address.returnPressed.connect(self._on_address)
        layout.addWidget(self.address)

        nav = QHBoxLayout()
        nav.setSpacing(4)
        self.back_button = QPushButton("←")
        self.forward_button = QPushButton("→")
        self.reload_button = QPushButton("⟳")
        for button in (self.back_button, self.forward_button, self.reload_button):
            button.setObjectName("webNavButton")
        self.back_button.clicked.connect(lambda: self.view.back())
        self.forward_button.clicked.connect(lambda: self.view.forward())
        self.reload_button.clicked.connect(lambda: self.view.reload())
        nav.addWidget(self.back_button)
        nav.addWidget(self.forward_button)
        nav.addWidget(self.reload_button)
        nav.addStretch()
        layout.addLayout(nav)

        _apply_dark_chromium_flags()
        from PySide6.QtWebEngineWidgets import QWebEngineView

        self.view = QWebEngineView(self)
        self.view.urlChanged.connect(self._on_url_changed)
        layout.addWidget(self.view, 1)
        self.apply_colors(True)

    def apply_colors(self, dark: bool) -> None:
        """Paint the web view background to match the app theme."""
        try:
            from PySide6.QtGui import QColor

            self.view.page().setBackgroundColor(QColor("#111827" if dark else "#ffffff"))
        except Exception:
            pass

    def set_search_base(self, url: str) -> None:
        self.search_base = (url or "").rstrip("/")

    def _on_url_changed(self, url: QUrl) -> None:
        self.address.setText(url.toString())

    def _on_address(self) -> None:
        text = self.address.text().strip()
        if not text:
            return
        if looks_like_url(text):
            self.open_url(text)
        else:
            self.search(text)

    def open_url(self, url: str) -> None:
        self.view.load(QUrl(normalize_url(url)))

    def search(self, query: str) -> None:
        if self.search_base:
            self.open_url(f"{self.search_base}/search?q={quote_plus(query)}")
        else:
            self.open_url(_DUCKDUCKGO + quote_plus(query))


class FallbackBrowserPanel(QWidget):
    """Widget-based panel used when QtWebEngine is unavailable."""

    def __init__(self, web_search=None, search_base: str = "", parent=None):
        super().__init__(parent)
        self.web_search = web_search
        self.search_base = (search_base or "").rstrip("/")
        self.setObjectName("webPanel")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self.address = QLineEdit()
        self.address.setObjectName("webAddress")
        self.address.setPlaceholderText("URL или поисковый запрос, затем Enter")
        self.address.setClearButtonEnabled(True)
        self.address.setMinimumWidth(320)
        self.address.returnPressed.connect(self._on_address)
        layout.addWidget(self.address)

        header = QLabel("Браузер (упрощённый режим: QtWebEngine недоступен)")
        header.setObjectName("webPanelHeader")
        header.setWordWrap(True)
        layout.addWidget(header)

        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setHtml("<p>Введите запрос в чате или откройте ссылку из ответа.</p>")
        layout.addWidget(self.view, 1)

    def set_search_base(self, url: str) -> None:
        self.search_base = (url or "").rstrip("/")

    def _on_address(self) -> None:
        text = self.address.text().strip()
        if not text:
            return
        if looks_like_url(text):
            self.open_url(normalize_url(text))
        else:
            self.search(text)

    def apply_colors(self, dark: bool) -> None:
        """Fallback panel is styled via QSS; nothing to paint here."""

    def open_url(self, url: str) -> None:
        safe = html.escape(url, quote=True)
        self.view.setHtml(
            f"<p>Ссылка открыта во внешнем браузере:</p><p><a href=\"{safe}\">{safe}</a></p>"
        )
        if os.environ.get("QT_QPA_PLATFORM") not in ("offscreen", "minimal"):
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(url))

    def search(self, query: str) -> None:
        if self.web_search is None:
            ddg = _DUCKDUCKGO + quote_plus(query)
            self.view.setHtml(
                f"<p><b>Запрос:</b> {html.escape(query)}</p>"
                f"<p>Веб-поиск не настроен. Встроенный движок недоступен.</p>"
                f'<p><a href="{html.escape(ddg, quote=True)}">Искать «{html.escape(query)}» в DuckDuckGo</a></p>'
            )
            return
        try:
            contexts = self.web_search.run(query)
        except Exception as exc:
            self.view.setHtml(
                f"<p><b>Запрос:</b> {html.escape(query)}</p><p>Ошибка поиска: {html.escape(str(exc))}</p>"
            )
            return
        parts = [f"<p><b>Запрос:</b> {html.escape(query)}</p>"]
        if not contexts:
            parts.append("<p>Ничего не найдено.</p>")
        for ctx in contexts:
            url = getattr(ctx, "url", "")
            title = getattr(ctx, "title", "") or url
            snippet = getattr(ctx, "snippet", "") or getattr(ctx, "content", "")
            href = html.escape(url, quote=True)
            parts.append(
                f'<p><a href="{href}">{html.escape(title)}</a><br>{html.escape(snippet[:300])}</p>'
            )
        self.view.setHtml("".join(parts))


def create_browser_panel(web_search=None, search_base: str = "", parent=None) -> QWidget:
    """Create the best available browser panel; never raises."""
    if webengine_enabled():
        try:
            return BrowserPanel(search_base=search_base, parent=parent)
        except Exception:
            pass
    return FallbackBrowserPanel(web_search=web_search, search_base=search_base, parent=parent)
