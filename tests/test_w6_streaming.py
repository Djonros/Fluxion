"""Tests for Phase W6 — SSE streaming (sessions chat + agent steps).

Covers:
- POST /api/sessions/{id}/chat with stream=true:
  - SSE protocol: event: meta → data: <token> … → event: done
  - content-type text/event-stream
  - assistant message persisted after the stream ends (fresh DB session)
  - message_count / seq / auto-title correctness
  - done event carries message IDs
  - 404 for foreign/nonexistent session, 401 without auth
- POST /api/agent/run with stream=true:
  - SSE protocol: event: step … → event: done
  - step payloads (thought/tool_name/observation) from scripted backend
  - done carries success/final_answer/iterations_used
- Non-streaming behavior unchanged (W3 contract)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base, get_db
from server.usage.limiter import RateLimiter, set_limiter


@pytest.fixture()
def mock_settings(tmp_path):
    settings = MagicMock()
    settings.model = "test-model"
    settings.generation.num_ctx = 4096
    settings.generation.max_tokens = 512
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
def client(mock_settings, tmp_path):
    db_path = tmp_path / "test_w6.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=eng)

    def _override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    with patch("server.app.OllamaBackend") as mock_be, \
         patch("server.app.WebSearch") as mock_ws:
        mock_be.return_value.is_available.return_value = True
        mock_ws.from_settings.return_value = MagicMock()

        from server.app import create_app
        app = create_app(mock_settings)
        app.dependency_overrides[get_db] = _override_get_db
        app.state.session_factory = TestSession
        test_limiter = RateLimiter(limits={
            "auth": 30, "chat": 30, "crud": 100, "usage": 100, "billing": 30,
            "default": 60,
        })
        set_limiter(test_limiter)
        app.state.rate_limiter = test_limiter
        c = TestClient(app)
        c._session = TestSession
        yield c
    set_limiter(RateLimiter())  # restore defaults


def _auth(client, email="alice@fluxion.dev", username="alice"):
    r = client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "SuperSecret123",
    })
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _create_session(client, hdr, title="Test Chat"):
    r = client.post("/api/sessions", json={"title": title}, headers=hdr)
    assert r.status_code == 201
    return r.json()["id"]


def _mock_assistant(client, tokens):
    """Patch the assistant's ask_stream to yield *tokens*."""
    ask_result = MagicMock()
    ask_result.strategy.name = "DIRECT"
    ask_result.rag_used = False
    ask_result.rag_sources = []
    ask_result.web_used = False
    ask_result.web_sources = []
    client.app.state.assistant.ask_stream = MagicMock(
        return_value=(ask_result, iter(tokens)),
    )
    return ask_result


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """Parse SSE text into [(event, payload)] — data lines are JSON strings."""
    events: list[tuple[str, dict]] = []
    current_event = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: "):].strip()
        elif line.startswith("data: "):
            data = line[len("data: "):]
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = data
            events.append((current_event, payload))
            current_event = None
    return events


# ═══════════════════════════════════════════════════════════════════════════════
#  Session chat streaming
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionChatStreaming:
    def test_sse_content_type_and_protocol(self, client):
        hdr = _auth(client)
        sid = _create_session(client, hdr)
        _mock_assistant(client, ["Hello ", "world"])

        r = client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "hi", "stream": True}, headers=hdr,
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        names = [name for name, _ in events]
        assert names[0] == "meta"
        assert names[-1] == "done"

        meta = events[0][1]
        assert meta["strategy"] == "DIRECT"
        assert meta["rag_used"] is False

        tokens = [p for name, p in events if name is None]
        assert "".join(tokens) == "Hello world"

    def test_assistant_message_persisted_after_stream(self, client):
        hdr = _auth(client)
        sid = _create_session(client, hdr)
        _mock_assistant(client, ["Chunk1", "Chunk2"])

        r = client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "q1", "stream": True}, headers=hdr,
        )
        assert r.status_code == 200

        msgs = client.get(f"/api/sessions/{sid}/messages", headers=hdr).json()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "q1"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "Chunk1Chunk2"
        assert msgs[1]["seq"] == 1
        assert msgs[1]["meta"]["strategy"] == "DIRECT"

        sess = client.get(f"/api/sessions/{sid}", headers=hdr).json()
        assert sess["message_count"] == 2

    def test_done_event_carries_message_ids(self, client):
        hdr = _auth(client)
        sid = _create_session(client, hdr)
        _mock_assistant(client, ["x"])

        r = client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "q", "stream": True}, headers=hdr,
        )
        events = _parse_sse(r.text)
        done = events[-1][1]
        assert events[-1][0] == "done"
        assert done["session_id"] == sid
        assert done["user_message_id"]
        assert done["assistant_message_id"]

        msgs = client.get(f"/api/sessions/{sid}/messages", headers=hdr).json()
        assert {m["id"] for m in msgs} == {
            done["user_message_id"], done["assistant_message_id"],
        }

    def test_auto_title_from_first_message(self, client):
        hdr = _auth(client)
        r = client.post("/api/sessions", json={}, headers=hdr)  # default title
        sid = r.json()["id"]
        _mock_assistant(client, ["r"])

        client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "Explain decorators in Python", "stream": True},
            headers=hdr,
        )
        sess = client.get(f"/api/sessions/{sid}", headers=hdr).json()
        assert sess["title"] == "Explain decorators in Python"

    def test_second_stream_message_appends_history(self, client):
        hdr = _auth(client)
        sid = _create_session(client, hdr)
        _mock_assistant(client, ["a"])

        client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "first", "stream": True}, headers=hdr,
        )
        client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "second", "stream": True}, headers=hdr,
        )
        msgs = client.get(f"/api/sessions/{sid}/messages", headers=hdr).json()
        assert [m["seq"] for m in msgs] == [0, 1, 2, 3]
        assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]

        # History passed to the assistant includes the first exchange
        _, kwargs = client.app.state.assistant.ask_stream.call_args
        history = kwargs["history"]
        assert history[0]["content"] == "first"
        assert history[1]["role"] == "assistant"

    def test_stream_false_keeps_json_contract(self, client):
        hdr = _auth(client)
        sid = _create_session(client, hdr)
        _mock_assistant(client, ["plain reply"])

        r = client.post(
            f"/api/sessions/{sid}/chat", json={"query": "q"}, headers=hdr,
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["assistant_message"]["content"] == "plain reply"

    def test_foreign_session_404(self, client):
        _auth(client)
        hdr_b = _auth(client, "b@f.dev", "bobxx")
        sid = _create_session(client, hdr_b)
        _mock_assistant(client, ["x"])

        r = client.post(
            f"/api/sessions/{sid}/chat",
            json={"query": "q", "stream": True},
            headers=_hdr(client),
        )
        assert r.status_code == 404

    def test_requires_auth(self, client):
        r = client.post(
            "/api/sessions/000/chat",
            json={"query": "q", "stream": True},
        )
        assert r.status_code == 401

    def test_nonexistent_session_404(self, client):
        hdr = _auth(client)
        r = client.post(
            "/api/sessions/00000000-0000-0000-0000-000000000000/chat",
            json={"query": "q", "stream": True}, headers=hdr,
        )
        assert r.status_code == 404


def _hdr(client, email="alice@fluxion.dev"):
    r = client.post("/api/auth/login", json={
        "email": email, "password": "SuperSecret123",
    })
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ═══════════════════════════════════════════════════════════════════════════════
#  Agent SSE streaming
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentSSEStreaming:
    def test_steps_and_done(self, client, tmp_path):
        hdr = _auth(client)
        (tmp_path / "test.txt").write_text("hello world", encoding="utf-8")

        backend = client.app.state.backend
        backend.generate = MagicMock(side_effect=[
            "Thought: I should read the file first.\nAction: read_file test.txt",
            "Thought: I have the answer.\nAction: finish File says hello world",
        ])

        r = client.post("/api/agent/run", json={
            "task": "read test.txt and summarize", "stream": True,
        }, headers=hdr)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")

        events = _parse_sse(r.text)
        steps = [p for name, p in events if name == "step"]
        dones = [p for name, p in events if name == "done"]

        assert len(steps) == 2
        assert steps[0]["iteration"] == 1
        assert steps[0]["tool_name"] == "read_file"
        assert steps[0]["tool_args"] == "test.txt"
        assert "hello world" in steps[0]["observation"]
        assert steps[0]["is_final"] is False

        assert steps[1]["iteration"] == 2
        assert steps[1]["is_final"] is True

        assert len(dones) == 1
        assert dones[0]["success"] is True
        assert dones[0]["final_answer"] == "File says hello world"
        assert dones[0]["iterations_used"] == 2

    def test_backend_error_still_emits_done(self, client):
        hdr = _auth(client)
        backend = client.app.state.backend
        backend.generate = MagicMock(side_effect=RuntimeError("boom"))

        r = client.post("/api/agent/run", json={
            "task": "anything", "stream": True,
        }, headers=hdr)
        assert r.status_code == 200
        events = _parse_sse(r.text)
        dones = [p for name, p in events if name == "done"]
        assert len(dones) == 1
        assert dones[0]["success"] is False
        assert "Backend error" in dones[0]["final_answer"]

    def test_non_stream_unchanged(self, client):
        hdr = _auth(client)
        backend = client.app.state.backend
        backend.generate = MagicMock(return_value="Action: finish ok")

        r = client.post("/api/agent/run", json={"task": "t"}, headers=hdr)
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        assert r.json()["success"] is True
        assert r.json()["final_answer"] == "ok"

    def test_requires_auth(self, client):
        r = client.post("/api/agent/run", json={"task": "t", "stream": True})
        assert r.status_code == 401
