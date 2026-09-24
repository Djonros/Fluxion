"""Web search module: SearXNG client, content fetcher, cache, pipeline."""
from .searxng import SearXNGClient, SearchResult, SearXNGError
from .fetcher import ContentFetcher, FetchedPage
from .cache import WebCache
from .pipeline import WebContext, WebSearch

__all__ = [
    "SearXNGClient",
    "SearchResult",
    "SearXNGError",
    "ContentFetcher",
    "FetchedPage",
    "WebCache",
    "WebContext",
    "WebSearch",
]
