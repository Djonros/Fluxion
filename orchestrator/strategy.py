"""Strategy enum and QuerySignals dataclass for routing decisions."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Strategy(Enum):
    """How a user query should be handled."""

    DIRECT = "direct"          # answer directly from LLM knowledge
    RAG = "rag"                # augment with indexed codebase context
    WEB = "web"                # needs live web search (Phase 4)
    RAG_THEN_WEB = "rag_then_web"  # try RAG first, fall back to web
    AGENT = "agent"            # multi-step ReAct agent (Phase 7)


@dataclass
class QuerySignals:
    """Structured signals extracted from the user's query."""
    file_paths: list[str] = field(default_factory=list)
    identifiers: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    mentions_version: bool = False
    mentions_local: bool = False
    mentions_external_lib: bool = False
    rag_score: float = 0.0
    web_score: float = 0.0
    direct_score: float = 0.0
    strategy: Strategy = Strategy.DIRECT
    confidence: float = 0.0
