"""Router: rule-based query classification (~0ms, no LLM call).

Routing logic:
  - Query references local file/identifier        → RAG
  - Query asks about latest/version/changelog      → WEB
  - Generic Python question ("how to", "explain")  → DIRECT
  - Mixed or low-confidence signals                → RAG_THEN_WEB
"""
from __future__ import annotations

import re
from pathlib import Path

from .strategy import QuerySignals, Strategy

# ── Pattern banks ─────────────────────────────────────────────────────────────

_FILE_PATH_RE = re.compile(
    r'(?:[\w.]+/)*[\w]+\.(?:py|js|ts|tsx|jsx|md|rst|txt|yaml|yml|toml|cfg|ini|java|go|rs|c|cpp|h|hpp|sh|sql)',
    re.IGNORECASE,
)

_IDENT_RE = re.compile(r'\b(?:def|class|func|function|method)\s+(\w+)', re.IGNORECASE)
_BACKTICK_IDENT_RE = re.compile(r'`([A-Za-z_][\w.]*)`')

_LOCAL_KEYWORDS = {
    "this project", "our code", "my code", "this codebase", "here in",
    "local", "indexed", "codebase", "repository", "repo",
}

_VERSION_KEYWORDS = {
    "latest", "newest", "current version", "changelog", "release notes",
    "new version", "deprecated", "migration", "upgrade to", "breaking change",
    "release", "announced", "2024", "2025", "2026",
}

_GENERIC_PREFIXES = (
    "how to", "how do i", "how can i", "what is", "what does",
    "explain", "difference between", "why does", "why is",
    "write a", "write me", "generate", "create a", "create an",
    "implement", "refactor", "debug", "fix",
)

_EXTERNAL_LIB_HINTS = {
    "pandas", "numpy", "django", "flask", "fastapi", "requests",
    "sqlalchemy", "pytest", "asyncio", "aiohttp", "httpx",
    "transformers", "pytorch", "tensorflow", "scikit-learn",
    "matplotlib", "seaborn", "beautifulsoup", "selenium",
}

_CODE_KEYWORDS = {
    "function", "class", "method", "variable", "import", "module",
    "decorator", "argument", "parameter", "return", "yield",
    "lambda", "generator", "iterator", "context manager",
}


class Router:
    """Classify a query into a Strategy using weighted rules.

    Classification is based on query signals alone; the caller (Assistant)
    handles availability fallback at execution time.
    """

    def classify(self, query: str) -> QuerySignals:
        low = query.lower()
        signals = self._extract_signals(low, query)
        self._score(signals, low)
        signals.strategy = self._decide(signals)
        return signals

    # ── signal extraction ──────────────────────────────────────────────────

    @staticmethod
    def _extract_signals(low: str, original: str) -> QuerySignals:
        sig = QuerySignals()

        # file paths
        sig.file_paths = _FILE_PATH_RE.findall(original)

        # identifiers from "def/class/function X" patterns
        for m in _IDENT_RE.finditer(original):
            sig.identifiers.append(m.group(1))

        # identifiers from backtick-wrapped names
        for m in _BACKTICK_IDENT_RE.finditer(original):
            name = m.group(1)
            if name not in sig.identifiers:
                sig.identifiers.append(name)

        # version / temporal signals
        sig.mentions_version = any(kw in low for kw in _VERSION_KEYWORDS)

        # local codebase signals
        sig.mentions_local = any(kw in low for kw in _LOCAL_KEYWORDS)

        # external library signals
        sig.mentions_external_lib = any(
            lib in low for lib in _EXTERNAL_LIB_HINTS
        )

        # keywords
        sig.keywords = [kw for kw in _CODE_KEYWORDS if kw in low]

        return sig

    # ── scoring ────────────────────────────────────────────────────────────

    def _score(self, sig: QuerySignals, low: str) -> None:
        # ── RAG score ──
        rag = 0.0
        if sig.file_paths:
            rag += 3.0
        if sig.identifiers:
            rag += 1.5
        if sig.mentions_local:
            rag += 2.0
        if sig.keywords:
            rag += 0.5
        sig.rag_score = rag

        # ── WEB score ──
        web = 0.0
        if sig.mentions_version:
            web += 3.0
        if sig.mentions_external_lib and not sig.mentions_local:
            web += 0.5
        sig.web_score = web

        # ── DIRECT score ──
        direct = 0.0
        if any(low.startswith(pfx) for pfx in _GENERIC_PREFIXES):
            direct += 2.0
        if not sig.file_paths and not sig.mentions_local:
            direct += 0.5
        sig.direct_score = direct

    def _decide(self, sig: QuerySignals) -> Strategy:
        scores: dict[Strategy, float] = {
            Strategy.DIRECT: sig.direct_score,
            Strategy.RAG: sig.rag_score,
            Strategy.WEB: sig.web_score,
        }

        best = max(scores, key=lambda s: scores[s])
        best_val = scores[best]

        # weak overall → direct
        if best_val < 1.0:
            return Strategy.DIRECT

        # version queries without strong local refs → WEB
        if best == Strategy.RAG and sig.web_score >= 2.5 and sig.rag_score < 3.0:
            return Strategy.WEB

        # ambiguity: RAG and WEB both moderate
        if sig.rag_score >= 1.5 and sig.web_score >= 2.0:
            return Strategy.RAG_THEN_WEB

        sig.confidence = min(1.0, best_val / 3.5)
        return best
