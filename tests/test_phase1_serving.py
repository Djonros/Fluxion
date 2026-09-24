"""Phase 1 milestone test: generate() returns non-empty text containing code.

Skipped automatically when Ollama or the model is unavailable.
"""
from __future__ import annotations

import pytest

from core.config import Settings
from core.inference import OllamaBackend


@pytest.fixture(scope="module")
def backend() -> OllamaBackend:
    return OllamaBackend(Settings.load())


@pytest.fixture(scope="module")
def _available(backend: OllamaBackend):
    if not backend.is_available():
        pytest.skip("Ollama server / model not available")


def test_generate_returns_code(backend: OllamaBackend, _available):
    messages = [
        {"role": "system", "content": "You are a Python coding assistant."},
        {"role": "user", "content": "Write a one-line Python function that returns the square of n."},
    ]
    reply = backend.generate(messages)
    assert isinstance(reply, str)
    assert reply.strip(), "empty response"
    assert "def " in reply, "response should contain a function definition"


def test_stream_yields_text(backend: OllamaBackend, _available):
    messages = [
        {"role": "user", "content": "Print hello world in Python. One line only."},
    ]
    chunks = list(backend.stream(messages))
    joined = "".join(chunks)
    assert joined.strip(), "stream produced no text"
