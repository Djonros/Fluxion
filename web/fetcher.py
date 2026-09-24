"""Content fetcher: retrieves web pages and extracts clean text via trafilatura."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 10
_DEFAULT_UA = "Mozilla/5.0 (compatible; VibeCoderBot/1.0)"
_MAX_CONTENT_CHARS = 8000


@dataclass
class FetchedPage:
    url: str
    title: str
    content: str
    success: bool = True
    error: str = ""


class ContentFetcher:
    """Fetches URLs and extracts main-text content."""

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        max_chars: int = _MAX_CONTENT_CHARS,
        user_agent: str = _DEFAULT_UA,
    ):
        self.timeout = timeout
        self.max_chars = max_chars
        self.headers = {"User-Agent": user_agent}

    def fetch(self, url: str) -> FetchedPage:
        """Download *url* and extract clean text. Returns FetchedPage."""
        try:
            resp = httpx.get(
                url,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.RequestError as exc:
            return FetchedPage(url=url, title="", content="", success=False, error=str(exc))
        except httpx.HTTPStatusError as exc:
            return FetchedPage(url=url, title="", content="", success=False, error=f"HTTP {exc.response.status_code}")

        html = resp.text
        return self._extract(url, html)

    @property
    def brief_mode(self) -> bool:
        """True when full-page extraction (trafilatura) is unavailable.

        In that mode web results carry only search snippets — the UI must
        tell the user (Lite bundle ships without trafilatura).
        """
        try:
            import trafilatura  # noqa: F401
        except Exception:
            return True
        return False

    def _extract(self, url: str, html: str) -> FetchedPage:
        try:
            import trafilatura

            extracted = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                output_format="txt",
                with_metadata=True,
            )
        except Exception as exc:
            return FetchedPage(url=url, title="", content="", success=False, error=f"extract: {exc}")

        if not extracted:
            return FetchedPage(url=url, title="", content="", success=False, error="no content extracted")

        title = ""
        try:
            metadata = trafilatura.extract(
                html, output_format="txt", with_metadata=True, include_comments=False
            )
            if metadata:
                for line in metadata.split("\n"):
                    if line.lower().startswith("title:"):
                        title = line.split(":", 1)[1].strip()
                        break
        except Exception:
            pass

        content = extracted.strip()
        if len(content) > self.max_chars:
            content = content[:self.max_chars] + "..."

        return FetchedPage(url=url, title=title, content=content)
