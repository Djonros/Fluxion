"""Tests for Phase W3 — chat sessions & conversation persistence.

Covers:
- Session CRUD lifecycle
- Message listing
- In-session chat (persists user + assistant messages, returns AI reply)
- Auto-title from first message
- Ownership enforcement
- Cascade delete (session deletion removes messages)
- Archive filtering
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base, get_db


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

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
def engine(tmp_path):
    db_path = tmp_path / "test_w3.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(mock_settings, engine):
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)

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
        c = TestClient(app)
        c._engine = engine
        c._session = TestSession
        return c


def _auth(client, email="alice@fluxion.dev", username="alice"):
    r = client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "SuperSecret123",
    })
    assert r.status_code == 201
    tokens = r.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def _create_session(client, hdr, title="Test Chat", project_id=None):
    payload = {"title": title}
    if project_id:
        payload["project_id"] = project_id
    r = client.post("/api/sessions", json=payload, headers=hdr)
    assert r.status_code == 201
    return r.json()


def _mock_assistant(client, response_text="AI reply"):
    """Patch the assistant's ask_stream to return a known response."""
    ask_result = MagicMock()
    ask_result.strategy.name = "DIRECT"
    ask_result.rag_used = False
    ask_result.rag_sources = []
    ask_result.web_used = False
    ask_result.web_sources = []
    client.app.state.assistant.ask_stream = MagicMock(
        return_value=(ask_result, iter([response_text])),
    )
    return ask_result


# ═══════════════════════════════════════════════════════════════════════════════
#  Session CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionCreate:
    def test_create_with_default_title(self, client):
        hdr = _auth(client)
        r = client.post("/api/sessions", json={}, headers=hdr)
        assert r.status_code == 201
        assert r.json()["title"] == "New Chat"
        assert r.json()["message_count"] == 0
        assert r.json()["is_archived"] is False

    def test_create_with_custom_title(self, client):
        hdr = _auth(client)
        r = client.post("/api/sessions", json={"title": "My Session"}, headers=hdr)
        assert r.status_code == 201
        assert r.json()["title"] == "My Session"

    def test_create_with_project_id(self, client):
        hdr = _auth(client)
        pid = client.post("/api/projects", json={"name": "P"}, headers=hdr).json()["id"]
        r = client.post("/api/sessions", json={"project_id": pid}, headers=hdr)
        assert r.status_code == 201
        assert r.json()["project_id"] == pid

    def test_create_requires_auth(self, client):
        r = client.post("/api/sessions", json={})
        assert r.status_code == 401


class TestSessionList:
    def test_list_empty(self, client):
        hdr = _auth(client)
        r = client.get("/api/sessions", headers=hdr)
        assert r.json() == []

    def test_list_multiple(self, client):
        hdr = _auth(client)
        for i in range(3):
            _create_session(client, hdr, title=f"S{i}")
        r = client.get("/api/sessions", headers=hdr)
        assert len(r.json()) == 3

    def test_list_excludes_archived_by_default(self, client):
        hdr = _auth(client)
        s1 = _create_session(client, hdr, title="Active")
        s2 = _create_session(client, hdr, title="ToArchive")
        client.patch(f"/api/sessions/{s2['id']}", json={"is_archived": True}, headers=hdr)
        r = client.get("/api/sessions", headers=hdr)
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "Active"

    def test_list_archived_only(self, client):
        hdr = _auth(client)
        s1 = _create_session(client, hdr, title="Active")
        s2 = _create_session(client, hdr, title="Archived")
        client.patch(f"/api/sessions/{s2['id']}", json={"is_archived": True}, headers=hdr)
        r = client.get("/api/sessions?archived=true", headers=hdr)
        assert len(r.json()) == 1
        assert r.json()[0]["title"] == "Archived"

    def test_list_requires_auth(self, client):
        r = client.get("/api/sessions")
        assert r.status_code == 401


class TestSessionGet:
    def test_get_success(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr, title="FindMe")
        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.status_code == 200
        assert r.json()["title"] == "FindMe"

    def test_get_nonexistent(self, client):
        hdr = _auth(client)
        r = client.get("/api/sessions/nope", headers=hdr)
        assert r.status_code == 404

    def test_get_requires_auth(self, client):
        r = client.get("/api/sessions/x")
        assert r.status_code == 401


class TestSessionUpdate:
    def test_update_title(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr, title="Old")
        r = client.patch(f"/api/sessions/{s['id']}", json={"title": "New Title"}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["title"] == "New Title"

    def test_archive(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        r = client.patch(f"/api/sessions/{s['id']}", json={"is_archived": True}, headers=hdr)
        assert r.json()["is_archived"] is True

    def test_update_nonexistent(self, client):
        hdr = _auth(client)
        r = client.patch("/api/sessions/nope", json={"title": "X"}, headers=hdr)
        assert r.status_code == 404


class TestSessionDelete:
    def test_delete_success(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        r = client.delete(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.status_code == 204
        assert client.get(f"/api/sessions/{s['id']}", headers=hdr).status_code == 404

    def test_delete_cascades_messages(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        _mock_assistant(client, "Hello")
        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "hi"}, headers=hdr)
        msgs_before = client.get(f"/api/sessions/{s['id']}/messages", headers=hdr)
        assert len(msgs_before.json()) == 2

        client.delete(f"/api/sessions/{s['id']}", headers=hdr)

        from server.chat.models import ChatMessage
        db = client.app.dependency_overrides[get_db]()
        # Re-create a session to get a fresh DB connection
        sess = next(client.app.dependency_overrides[get_db]())
        remaining = sess.query(ChatMessage).filter(ChatMessage.session_id == s["id"]).count()
        sess.close()
        assert remaining == 0

    def test_delete_nonexistent(self, client):
        hdr = _auth(client)
        r = client.delete("/api/sessions/nope", headers=hdr)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
#  Messages
# ═══════════════════════════════════════════════════════════════════════════════

class TestMessageList:
    def test_empty_session_no_messages(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        r = client.get(f"/api/sessions/{s['id']}/messages", headers=hdr)
        assert r.json() == []

    def test_requires_auth(self, client):
        r = client.get("/api/sessions/x/messages")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  In-session chat
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionChat:
    def test_chat_persists_both_messages(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        _mock_assistant(client, "Hello back")

        r = client.post(f"/api/sessions/{s['id']}/chat", json={"query": "hi"}, headers=hdr)
        assert r.status_code == 200
        data = r.json()

        assert data["user_message"]["role"] == "user"
        assert data["user_message"]["content"] == "hi"
        assert data["user_message"]["seq"] == 0

        assert data["assistant_message"]["role"] == "assistant"
        assert data["assistant_message"]["content"] == "Hello back"
        assert data["assistant_message"]["seq"] == 1

        assert data["strategy"] == "DIRECT"

    def test_chat_increments_count(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        _mock_assistant(client, "Reply")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "first"}, headers=hdr)
        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.json()["message_count"] == 2

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "second"}, headers=hdr)
        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.json()["message_count"] == 4

    def test_messages_retrievable_after_chat(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        _mock_assistant(client, "World")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "hello"}, headers=hdr)

        msgs = client.get(f"/api/sessions/{s['id']}/messages", headers=hdr).json()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "hello"
        assert msgs[1]["role"] == "assistant"
        assert msgs[1]["content"] == "World"

    def test_multi_turn_conversation_history(self, client):
        """Second chat should include first exchange in history."""
        hdr = _auth(client)
        s = _create_session(client, hdr)
        ask_result = MagicMock()
        ask_result.strategy.name = "DIRECT"
        ask_result.rag_used = False
        ask_result.rag_sources = []
        ask_result.web_used = False
        ask_result.web_sources = []
        client.app.state.assistant.ask_stream = MagicMock(
            return_value=(ask_result, iter(["response"])),
        )

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "turn 1"}, headers=hdr)
        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "turn 2"}, headers=hdr)

        # Second call should have history from first exchange
        _, kwargs = client.app.state.assistant.ask_stream.call_args
        assert len(kwargs["history"]) == 2
        assert kwargs["history"][0]["content"] == "turn 1"
        assert kwargs["history"][1]["content"] == "response"

    def test_auto_title_from_first_message(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr, title="New Chat")
        _mock_assistant(client, "Reply")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "How do I use FastAPI?"}, headers=hdr)

        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.json()["title"] == "How do I use FastAPI?"

    def test_auto_title_only_on_first_message(self, client):
        """Title should not change on subsequent messages."""
        hdr = _auth(client)
        s = _create_session(client, hdr, title="New Chat")
        _mock_assistant(client, "Reply")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "first question"}, headers=hdr)
        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "second question"}, headers=hdr)

        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.json()["title"] == "first question"

    def test_custom_title_preserved(self, client):
        """If user set a custom title, auto-title should not override it."""
        hdr = _auth(client)
        s = _create_session(client, hdr, title="My Custom Title")
        _mock_assistant(client, "Reply")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "hello"}, headers=hdr)

        r = client.get(f"/api/sessions/{s['id']}", headers=hdr)
        assert r.json()["title"] == "My Custom Title"

    def test_chat_empty_query_rejected(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        r = client.post(f"/api/sessions/{s['id']}/chat", json={"query": ""}, headers=hdr)
        assert r.status_code == 422

    def test_chat_requires_auth(self, client):
        r = client.post("/api/sessions/x/chat", json={"query": "hi"})
        assert r.status_code == 401

    def test_chat_nonexistent_session(self, client):
        hdr = _auth(client)
        r = client.post("/api/sessions/nope/chat", json={"query": "hi"}, headers=hdr)
        assert r.status_code == 404

    def test_assistant_meta_stored_on_message(self, client):
        hdr = _auth(client)
        s = _create_session(client, hdr)
        _mock_assistant(client, "Reply")

        client.post(f"/api/sessions/{s['id']}/chat", json={"query": "test"}, headers=hdr)

        msgs = client.get(f"/api/sessions/{s['id']}/messages", headers=hdr).json()
        ai_msg = msgs[1]
        assert ai_msg["meta"]["strategy"] == "DIRECT"
        assert ai_msg["meta"]["rag_used"] is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Ownership
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionOwnership:
    def test_cannot_see_others_sessions(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobx")
        s = _create_session(client, hdr_a, title="Alice's chat")

        r = client.get(f"/api/sessions/{s['id']}", headers=hdr_b)
        assert r.status_code == 404

    def test_cannot_chat_in_others_sessions(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobx")
        s = _create_session(client, hdr_a)
        _mock_assistant(client, "Reply")

        r = client.post(f"/api/sessions/{s['id']}/chat", json={"query": "hack"}, headers=hdr_b)
        assert r.status_code == 404

    def test_cannot_delete_others_sessions(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobx")
        s = _create_session(client, hdr_a)

        r = client.delete(f"/api/sessions/{s['id']}", headers=hdr_b)
        assert r.status_code == 404

    def test_separate_users_separate_sessions(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobx")

        _create_session(client, hdr_a, title="A1")
        _create_session(client, hdr_a, title="A2")
        _create_session(client, hdr_b, title="B1")

        assert len(client.get("/api/sessions", headers=hdr_a).json()) == 2
        assert len(client.get("/api/sessions", headers=hdr_b).json()) == 1
