"""Phase 3 tests: Router classification, PromptBuilder, Assistant smoke.

Router tests are fast and deterministic (no model needed).
Assistant tests are skipped when Ollama is unavailable.
"""
from __future__ import annotations

import pytest

from core.config import Settings
from core.inference import OllamaBackend
from core.tokenizer_util import count_tokens
from orchestrator import (
    Assistant,
    PromptBuilder,
    PromptContext,
    Router,
    Strategy,
)


# ═══════════════════════════════════════════════════════════════════════════════
#  Router classification
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def router():
    return Router()


@pytest.mark.parametrize(
    "query, expected",
    [
        # DIRECT — generic Python questions
        ("How to read a CSV file in Python?", Strategy.DIRECT),
        ("What is a decorator?", Strategy.DIRECT),
        ("Explain the difference between list and tuple", Strategy.DIRECT),
        ("Write a function to sort a list", Strategy.DIRECT),
        ("How do I use async/await?", Strategy.DIRECT),

        # RAG — references local code, files, identifiers
        ("How does fetch_user work in core/config.py?", Strategy.RAG),
        ("Explain the UserRepo class in our codebase", Strategy.RAG),
        ("How does the chunker work in this project?", Strategy.RAG),
        ("Where is the `OllamaClient` class defined?", Strategy.RAG),
        ("What does Indexer do in rag/indexer.py?", Strategy.RAG),

        # WEB — version/latest/changelog
        ("What is the latest version of FastAPI?", Strategy.WEB),
        ("What changed in Django 5.0 changelog?", Strategy.WEB),
        ("What is the current release of transformers?", Strategy.WEB),
    ],
)
def test_router_classification(router: Router, query: str, expected: Strategy):
    sig = router.classify(query)
    assert sig.strategy == expected, (
        f"Expected {expected.value} for '{query}', got {sig.strategy.value} "
        f"(rag={sig.rag_score}, web={sig.web_score}, dir={sig.direct_score})"
    )


def test_router_extracts_file_paths(router: Router):
    sig = router.classify("How does fetch_user work in core/config.py?")
    assert any("config.py" in fp for fp in sig.file_paths)


def test_router_extracts_identifiers(router: Router):
    sig = router.classify("Explain the `fetch_user` function")
    assert "fetch_user" in sig.identifiers


def test_router_detects_version_keywords(router: Router):
    sig = router.classify("What is the latest version of pytorch?")
    assert sig.mentions_version is True


def test_router_detects_local_keywords(router: Router):
    sig = router.classify("How does this work in our codebase?")
    assert sig.mentions_local is True


def test_router_signals_have_confidence(router: Router):
    sig = router.classify("What is a decorator?")
    assert 0.0 <= sig.confidence <= 1.0


# ═══════════════════════════════════════════════════════════════════════════════
#  PromptBuilder
# ═══════════════════════════════════════════════════════════════════════════════

class TestPromptBuilder:
    def _builder(self):
        return PromptBuilder(num_ctx=4096, max_predict=512)

    def test_build_minimal_prompt(self):
        pb = self._builder()
        msgs = pb.build(PromptContext(query="Hello"))
        assert msgs[0]["role"] == "system"
        assert msgs[-1]["role"] == "user"
        assert msgs[-1]["content"] == "Hello"

    def test_rag_context_included(self):
        pb = self._builder()
        msgs = pb.build(PromptContext(
            query="How does this work?",
            rag_snippets=[("1", "def foo(): pass")],
        ))
        assert len(msgs) == 3
        rag_msg = msgs[1]
        assert "[1]" in rag_msg["content"]
        assert "def foo" in rag_msg["content"]

    def test_history_included(self):
        pb = self._builder()
        history = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
        ]
        msgs = pb.build(PromptContext(query="Tell me more", history=history))
        contents = [m["content"] for m in msgs]
        assert "What is Python?" in contents
        assert "Tell me more" in contents

    def test_history_truncated_under_budget(self):
        pb = PromptBuilder(num_ctx=1024, max_predict=256)
        history = [
            {"role": "user", "content": "x" * 5000},
            {"role": "assistant", "content": "y" * 5000},
            {"role": "user", "content": "recent question"},
            {"role": "assistant", "content": "recent answer"},
        ]
        msgs = pb.build(PromptContext(query="query", history=history))
        total_tokens = sum(count_tokens(m["content"]) for m in msgs)
        assert total_tokens < 1024

    def test_long_query_truncated(self):
        pb = self._builder()
        long_query = "A" * 20000
        msgs = pb.build(PromptContext(query=long_query))
        query_tokens = count_tokens(msgs[-1]["content"])
        assert query_tokens <= 2000

    def test_total_prompt_within_budget(self):
        pb = self._builder()
        rag_snippets = [(str(i), f"def func_{i}(): return {i}" * 20) for i in range(10)]
        msgs = pb.build(PromptContext(
            query="How does this work?",
            rag_snippets=rag_snippets,
        ))
        total = sum(count_tokens(m["content"]) for m in msgs)
        assert total <= 4096, f"prompt ({total} tokens) exceeds num_ctx (4096)"


# ═══════════════════════════════════════════════════════════════════════════════
#  Assistant smoke (needs Ollama)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def backend():
    b = OllamaBackend(Settings.load())
    if not b.is_available():
        pytest.skip("Ollama / model not available")
    return b


class TestAssistant:
    def test_classify_returns_signals(self, backend):
        asst = Assistant(backend, Settings.load())
        sig = asst.classify("How does fetch_user work in core/config.py?")
        assert sig.strategy == Strategy.RAG

    def test_classify_direct_query(self, backend):
        asst = Assistant(backend, Settings.load())
        sig = asst.classify("How to reverse a list in Python?")
        assert sig.strategy == Strategy.DIRECT

    def test_ask_streams_response(self, backend):
        asst = Assistant(backend, Settings.load())
        tokens = list(asst.ask("Say hello in Python. One line only."))
        assert "".join(tokens).strip()

    def test_ask_stream_returns_metadata(self, backend):
        asst = Assistant(backend, Settings.load())
        result, stream = asst.ask_stream("Print hi. One line.")
        assert result.strategy in (Strategy.DIRECT, Strategy.RAG, Strategy.WEB, Strategy.RAG_THEN_WEB)
        assert isinstance(result.signals.strategy, Strategy)
        tokens = list(stream)
        assert "".join(tokens).strip()
