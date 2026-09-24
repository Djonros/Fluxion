"""Tests for the Fluxion FastAPI server.

All tests use mocked backends — no real Ollama calls.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def mock_settings(tmp_path):
    """Minimal Settings mock for create_app."""
    settings = MagicMock()
    settings.model = "qwen2.5-coder:7b-instruct"
    settings.ollama_host = "http://localhost:11434"
    settings.generation.num_ctx = 32768
    settings.generation.max_tokens = 2048
    settings.rag.enabled = False
    settings.rag.embedding_model = "BAAI/bge-m3"
    settings.rag.embedding_device = "cpu"
    settings.rag.chroma_dir = str(tmp_path / "chroma")
    settings.rag.chunk_max_chars = 1500
    settings.rag.chunk_overlap = 200
    settings.rag.top_k = 8
    settings.rag.rerank = True
    settings.web.enabled = False
    settings.web.searxng_url = "http://localhost:8080"
    settings.web.max_pages = 3
    settings.web.timeout = 10
    settings.web.cache_ttl_hours = 24
    settings.web.cache_dir = str(tmp_path / "web_cache")
    settings.project_root = MagicMock(return_value=tmp_path)
    return settings


@pytest.fixture()
def client(mock_settings):
    """TestClient with mocked Ollama backend.

    Auth is overridden to return a mock user so existing tests focus on
    endpoint logic rather than JWT mechanics.
    """
    with patch("server.app.OllamaBackend") as mock_backend_cls, \
         patch("server.app.WebSearch") as mock_ws_cls:
        mock_backend = MagicMock()
        mock_backend.is_available.return_value = True
        mock_backend_cls.return_value = mock_backend

        mock_ws = MagicMock()
        mock_ws_cls.from_settings.return_value = mock_ws

        from server.app import create_app
        from server.auth.dependencies import get_current_user
        app = create_app(mock_settings)

        mock_user = MagicMock()
        mock_user.id = "test-user-id"
        mock_user.email = "test@fluxion.dev"
        mock_user.is_active = True
        app.dependency_overrides[get_current_user] = lambda: mock_user

        return TestClient(app)


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/health
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    def test_returns_200(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_returns_status_up(self, client):
        data = client.get("/api/health").json()
        assert data["status"] == "up"

    def test_returns_model_name(self, client, mock_settings):
        data = client.get("/api/health").json()
        assert data["model"] == mock_settings.model

    def test_returns_ollama_available(self, client):
        data = client.get("/api/health").json()
        assert data["ollama_available"] is True

    def test_returns_rag_enabled(self, client):
        data = client.get("/api/health").json()
        assert "rag_enabled" in data
        assert "rag_chunks" in data

    def test_returns_web_enabled(self, client):
        data = client.get("/api/health").json()
        assert "web_enabled" in data

    def test_ollama_unavailable(self, mock_settings):
        """When Ollama is down, health still returns 200 but ollama_available=False."""
        with patch("server.app.OllamaBackend") as mock_backend_cls, \
             patch("server.app.WebSearch") as mock_ws_cls:
            mock_backend = MagicMock()
            mock_backend.is_available.return_value = False
            mock_backend_cls.return_value = mock_backend
            mock_ws_cls.from_settings.return_value = MagicMock()

            from server.app import create_app
            app = create_app(mock_settings)
            c = TestClient(app)

            data = c.get("/api/health").json()
            assert data["ollama_available"] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/chat
# ═══════════════════════════════════════════════════════════════════════════════

class TestChatEndpoint:
    def test_chat_non_streaming(self, client):
        """Non-streaming chat returns full response as JSON."""
        assistant = client.app.state.assistant
        ask_result = MagicMock()
        ask_result.strategy.name = "DIRECT"
        ask_result.rag_used = False
        ask_result.rag_sources = []
        ask_result.web_used = False
        ask_result.web_sources = []

        assistant.ask_stream = MagicMock(
            return_value=(ask_result, iter(["Hello ", "world!"]))
        )

        r = client.post("/api/chat", json={"query": "hi", "stream": False})
        assert r.status_code == 200

        data = r.json()
        assert data["response"] == "Hello world!"
        assert data["strategy"] == "DIRECT"

    def test_chat_streaming(self, client):
        """Streaming chat returns SSE text/event-stream."""
        assistant = client.app.state.assistant
        ask_result = MagicMock()
        ask_result.strategy.name = "DIRECT"
        ask_result.rag_used = False
        ask_result.rag_sources = []
        ask_result.web_used = False
        ask_result.web_sources = []

        assistant.ask_stream = MagicMock(
            return_value=(ask_result, iter(["chunk1", "chunk2"]))
        )

        r = client.post("/api/chat", json={"query": "hi", "stream": True})
        assert r.status_code == 200
        assert "text/event-stream" in r.headers.get("content-type", "")

        body = r.text
        assert "data:" in body
        assert "chunk1" in body
        assert "chunk2" in body
        assert "done" in body

    def test_chat_with_history(self, client):
        """Chat accepts history parameter."""
        assistant = client.app.state.assistant
        ask_result = MagicMock()
        ask_result.strategy.name = "DIRECT"
        ask_result.rag_used = False
        ask_result.rag_sources = []
        ask_result.web_used = False
        ask_result.web_sources = []

        assistant.ask_stream = MagicMock(
            return_value=(ask_result, iter(["response"]))
        )

        history = [{"role": "user", "content": "previous question"}]
        r = client.post("/api/chat", json={"query": "follow up", "history": history, "stream": False})
        assert r.status_code == 200

        _, kwargs = assistant.ask_stream.call_args
        assert kwargs["history"] == history


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/agent/run
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentEndpoint:
    def test_agent_run_success(self, client):
        """Agent runs and returns result."""
        from orchestrator import AgentResult, AgentStep

        mock_result = AgentResult(
            steps=[AgentStep(iteration=1, is_final=True, final_answer="42")],
            final_answer="42",
            iterations_used=1,
            success=True,
        )

        with patch("server.app.CodingAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = mock_result
            mock_agent_cls.return_value = mock_agent

            r = client.post("/api/agent/run", json={"task": "What is 2+2?"})

        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert data["final_answer"] == "42"
        assert data["iterations_used"] == 1
        assert len(data["steps"]) == 1
        assert data["steps"][0]["is_final"] is True

    def test_agent_run_with_context(self, client):
        """Agent accepts context parameter."""
        from orchestrator import AgentResult

        mock_result = AgentResult(final_answer="done", iterations_used=1, success=True)

        with patch("server.app.CodingAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = mock_result
            mock_agent_cls.return_value = mock_agent

            r = client.post("/api/agent/run", json={
                "task": "fix bug",
                "context": "The error is in line 42",
            })

        assert r.status_code == 200
        mock_agent.run.assert_called_once_with("fix bug", context="The error is in line 42")

    def test_agent_allow_write_flag(self, client):
        """allow_write=True passes through to CodingAgent."""
        from orchestrator import AgentResult

        mock_result = AgentResult(final_answer="written", iterations_used=1, success=True)

        with patch("server.app.CodingAgent") as mock_agent_cls:
            mock_agent = MagicMock()
            mock_agent.run.return_value = mock_result
            mock_agent_cls.return_value = mock_agent

            r = client.post("/api/agent/run", json={
                "task": "create file",
                "allow_write": True,
            })

        assert r.status_code == 200
        _, kwargs = mock_agent_cls.call_args
        assert kwargs["allow_write"] is True


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/rag/index
# ═══════════════════════════════════════════════════════════════════════════════

class TestRagIndexEndpoint:
    def test_index_valid_path(self, client, tmp_path):
        """Indexing a real directory returns chunk count."""
        test_file = tmp_path / "mod.py"
        test_file.write_text("def hello():\n    return 'world'\n", encoding="utf-8")

        rag_service = client.app.state.rag_service
        rag_service.index = MagicMock(return_value=5)

        r = client.post("/api/rag/index", json={"path": str(tmp_path)})
        assert r.status_code == 200
        data = r.json()
        assert data["chunks_indexed"] == 5

    def test_index_nonexistent_path(self, client):
        """Indexing a non-existent path returns error."""
        r = client.post("/api/rag/index", json={"path": "/nonexistent/path"})
        assert r.status_code == 200
        data = r.json()
        assert "error" in data


# ═══════════════════════════════════════════════════════════════════════════════
#  CORS headers
# ═══════════════════════════════════════════════════════════════════════════════

class TestCORS:
    def test_cors_headers_present(self, client):
        """Health endpoint includes CORS headers."""
        r = client.options("/api/health", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        })
        assert r.status_code == 200
        allow_origin = r.headers.get("access-control-allow-origin", "")
        assert allow_origin in ("*", "http://localhost:3000")
