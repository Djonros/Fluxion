"""Phase 12 tests: Multi-model backends — APIBackend, BackendFactory.

All tests use httpx mocking or no external dependencies.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from core.api_backend import APIBackend
from core.backend_factory import BackendFactory, _detect_backend_type, _build_chain

from core.config import Settings, GenerationSettings
from core.inference import ModelBackend, OllamaBackend


# ═══════════════════════════════════════════════════════════════════════════════
#  APIBackend
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPIBackend:
    def test_is_available_with_key(self):
        backend = APIBackend(api_key="sk-test", model="qwen/test")
        assert backend.is_available() is True

    def test_is_available_without_key(self):
        backend = APIBackend(api_key="", model="qwen/test")
        assert backend.is_available() is False

    def test_generate_calls_api(self):
        """Non-streaming generate calls /chat/completions and returns content."""
        backend = APIBackend(api_key="sk-test", model="test-model")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello from API!"}}]
        }

        with patch("httpx.post", return_value=mock_response) as mock_post:
            result = backend.generate([{"role": "user", "content": "hi"}])

        assert result == "Hello from API!"
        call_args = mock_post.call_args
        assert "/chat/completions" in call_args.args[0]
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"

    def test_generate_raises_on_error(self):
        backend = APIBackend(api_key="sk-test")

        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"

        with patch("httpx.post", return_value=mock_response):
            with pytest.raises(RuntimeError, match="401"):
                backend.generate([{"role": "user", "content": "hi"}])

    def test_stream_yields_tokens(self):
        """Streaming generate yields delta content tokens."""
        backend = APIBackend(api_key="sk-test")

        sse_lines = [
            'data: {"choices": [{"delta": {"content": "Hello"}}]}',
            'data: {"choices": [{"delta": {"content": " world"}}]}',
            "data: [DONE]",
        ]

        mock_stream = MagicMock()
        mock_stream.status_code = 200
        mock_stream.__enter__ = MagicMock(return_value=mock_stream)
        mock_stream.__exit__ = MagicMock(return_value=False)
        mock_stream.iter_lines = MagicMock(return_value=iter(sse_lines))

        with patch("httpx.stream", return_value=mock_stream):
            tokens = list(backend.stream([{"role": "user", "content": "hi"}]))

        assert tokens == ["Hello", " world"]

    def test_from_settings(self, tmp_path):
        """from_settings reads env vars."""
        settings = Settings(model="fallback-model")

        with patch.dict(os.environ, {
            "FLUXION_API_KEY": "sk-from-env",
            "FLUXION_API_BASE_URL": "https://api.groq.com/openai/v1",
            "FLUXION_API_MODEL": "llama-3-70b",
        }):
            backend = APIBackend.from_settings(settings)

        assert backend.api_key == "sk-from-env"
        assert backend.base_url == "https://api.groq.com/openai/v1"
        assert backend.model == "llama-3-70b"

    def test_payload_structure(self):
        backend = APIBackend(api_key="sk-test", model="test-model")
        payload = backend._payload(
            [{"role": "user", "content": "hi"}],
            GenerationSettings(temperature=0.5, top_p=0.8, max_tokens=100),
            stream=True,
        )
        assert payload["model"] == "test-model"
        assert payload["temperature"] == 0.5
        assert payload["top_p"] == 0.8
        assert payload["max_tokens"] == 100
        assert payload["stream"] is True

    def test_implements_model_backend(self):
        """APIBackend satisfies the ModelBackend ABC interface."""
        backend = APIBackend(api_key="sk-test")
        assert isinstance(backend, ModelBackend)


# ═══════════════════════════════════════════════════════════════════════════════
#  BackendFactory
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackendDetection:
    def test_detect_ollama_by_default(self):
        settings = Settings()
        with patch.dict(os.environ, {}, clear=True):
            # Need to also remove FLUXION_BACKEND and FLUXION_API_KEY
            env = {k: v for k, v in os.environ.items()}
            env.pop("FLUXION_BACKEND", None)
            env.pop("FLUXION_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                assert _detect_backend_type(settings) == "ollama"

    def test_detect_llama_cpp_when_engine_installed_without_model(self):
        """Clean machine: engine shipped, model not downloaded yet -> llama.cpp.

        Regression: requiring a downloaded model sent a fresh install to Ollama,
        so the app could not start without it."""
        with patch.dict(os.environ, {}, clear=True), \
                patch("core.backend_factory._llama_cpp_installed", return_value=True):
            assert _detect_backend_type(Settings()) == "llama_cpp"
            # an explicit choice still wins
            assert _detect_backend_type(Settings(backend="ollama")) == "ollama"

    def test_no_llama_cpp_package_keeps_ollama_default(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch("core.backend_factory._llama_cpp_installed", return_value=False):
            assert _detect_backend_type(Settings()) == "ollama"

    def test_detect_api_by_env_key(self):
        settings = Settings()
        with patch.dict(os.environ, {"FLUXION_API_KEY": "sk-test"}, clear=True):
            assert _detect_backend_type(settings) == "api"

    def test_detect_explicit_backend(self):
        settings = Settings()
        with patch.dict(os.environ, {"FLUXION_BACKEND": "api"}, clear=True):
            assert _detect_backend_type(settings) == "api"

    def test_detect_explicit_llama_cpp(self):
        settings = Settings()
        with patch.dict(os.environ, {"FLUXION_BACKEND": "llama_cpp"}, clear=True):
            assert _detect_backend_type(settings) == "llama_cpp"

    def test_chain_starts_with_primary(self):
        chain = _build_chain("api")
        assert chain[0] == "api"
        assert "ollama" in chain
        assert len(chain) == 3

    def test_chain_no_duplicates(self):
        chain = _build_chain("ollama")
        assert len(chain) == len(set(chain))


class TestBackendFactory:
    def test_create_ollama(self):
        """Factory creates OllamaBackend when it's available."""
        settings = Settings()

        with patch.dict(os.environ, {}, clear=True):
            with patch("core.backend_factory.OllamaBackend") as mock_ollama_cls:
                mock_backend = MagicMock()
                mock_backend.is_available.return_value = True
                mock_ollama_cls.return_value = mock_backend

                result = BackendFactory.create(settings)

            assert result is mock_backend

    def test_create_fallback_to_api(self):
        """Factory falls back to APIBackend when Ollama is unavailable."""
        settings = Settings()

        with patch.dict(os.environ, {}, clear=True):
            with patch("core.backend_factory.OllamaBackend") as mock_ollama_cls, \
                 patch("core.api_backend.APIBackend") as mock_api_cls:
                mock_ollama = MagicMock()
                mock_ollama.is_available.return_value = False
                mock_ollama_cls.return_value = mock_ollama

                mock_api = MagicMock()
                mock_api.is_available.return_value = True
                mock_api_cls.from_settings.return_value = mock_api

                result = BackendFactory.create(settings)

            assert result is mock_api

    def test_create_raises_when_all_unavailable(self):
        settings = Settings()

        with patch.dict(os.environ, {}, clear=True):
            with patch("core.backend_factory.OllamaBackend") as mock_ollama_cls:
                mock_ollama = MagicMock()
                mock_ollama.is_available.return_value = False
                mock_ollama_cls.return_value = mock_ollama

                with patch("core.api_backend.APIBackend") as mock_api_cls:
                    mock_api = MagicMock()
                    mock_api.is_available.return_value = False
                    mock_api_cls.from_settings.return_value = mock_api

                    with pytest.raises(RuntimeError, match="No backend available"):
                        BackendFactory.create(settings)

    def test_create_primary_no_fallback(self):
        """create_primary returns the configured backend even if unavailable."""
        settings = Settings()

        with patch.dict(os.environ, {"FLUXION_BACKEND": "api"}, clear=True):
            with patch("core.api_backend.APIBackend") as mock_api_cls:
                mock_api = MagicMock()
                mock_api.is_available.return_value = False
                mock_api_cls.from_settings.return_value = mock_api

                result = BackendFactory.create_primary(settings)

            assert result is mock_api


# ═══════════════════════════════════════════════════════════════════════════════
#  LlamaCppBackend (import only — actual model load requires llama_cpp)
# ═══════════════════════════════════════════════════════════════════════════════

class TestLlamaCppBackend:
    def test_import_error_without_llama_cpp(self):
        """from_settings raises ImportError when llama_cpp is not installed."""
        from core.llama_cpp_backend import LlamaCppBackend

        with patch.dict(os.environ, {"FLUXION_GGUF_PATH": "/fake/path.gguf"}, clear=True):
            with patch("builtins.__import__", side_effect=ImportError("no llama_cpp")):
                with pytest.raises(ImportError, match="llama-cpp-python"):
                    LlamaCppBackend.from_settings(Settings())

    def test_runtime_error_without_gguf_path(self):
        from core.llama_cpp_backend import LlamaCppBackend

        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(RuntimeError, match="FLUXION_GGUF_PATH"):
                LlamaCppBackend.from_settings(Settings())
