"""Tests for Phase W2 — protected API endpoints + project management.

Covers:
- Existing endpoints (chat, agent, rag) reject unauthenticated requests
- Project CRUD lifecycle
- Ownership enforcement (user A cannot see/edit/delete user B's projects)
- Partial updates
- Validation errors
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
    """Isolated SQLite engine for each test."""
    db_path = tmp_path / "test_w2.db"
    eng = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=eng)
    return eng


@pytest.fixture()
def client(mock_settings, engine):
    """TestClient with mocked backends and isolated DB."""
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


def _auth_user(client, email="alice@fluxion.dev", username="alice"):
    """Register and return (headers, user_id)."""
    r = client.post("/api/auth/register", json={
        "email": email,
        "username": username,
        "password": "SuperSecret123",
    })
    assert r.status_code == 201, r.text
    tokens = r.json()
    return {"Authorization": f"Bearer {tokens['access_token']}"}, r.json()


# ═══════════════════════════════════════════════════════════════════════════════
#  Protected endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectedEndpoints:
    def test_chat_requires_auth(self, client):
        r = client.post("/api/chat", json={"query": "hello"})
        assert r.status_code == 401

    def test_agent_requires_auth(self, client):
        r = client.post("/api/agent/run", json={"task": "do something"})
        assert r.status_code == 401

    def test_rag_index_requires_auth(self, client):
        r = client.post("/api/rag/index", json={"path": "/tmp"})
        assert r.status_code == 401

    def test_chat_works_with_token(self, client):
        hdr, _ = _auth_user(client)
        assistant = client.app.state.assistant
        ask_result = MagicMock()
        ask_result.strategy.name = "DIRECT"
        ask_result.rag_used = False
        ask_result.rag_sources = []
        ask_result.web_used = False
        ask_result.web_sources = []
        assistant.ask_stream = MagicMock(
            return_value=(ask_result, iter(["hi"])),
        )
        r = client.post("/api/chat", json={"query": "hello", "stream": False}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["response"] == "hi"

    def test_agent_works_with_token(self, client):
        hdr, _ = _auth_user(client)
        from orchestrator import AgentResult

        with patch("server.app.CodingAgent") as mock_cls:
            mock_cls.return_value.run.return_value = AgentResult(
                final_answer="done", iterations_used=1, success=True,
            )
            r = client.post("/api/agent/run", json={"task": "test"}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["success"] is True


# ═══════════════════════════════════════════════════════════════════════════════
#  Project CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestProjectCreate:
    def test_create_success(self, client):
        hdr, _ = _auth_user(client)
        r = client.post("/api/projects", json={
            "name": "My Project",
            "description": "A test project",
        }, headers=hdr)
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "My Project"
        assert data["description"] == "A test project"
        assert data["settings"] == {}
        assert data["is_active"] is True
        assert "id" in data
        assert "user_id" in data
        assert "created_at" in data

    def test_create_with_root_path(self, client):
        hdr, _ = _auth_user(client)
        r = client.post("/api/projects", json={
            "name": "Local",
            "root_path": "/home/user/repo",
        }, headers=hdr)
        assert r.status_code == 201
        assert r.json()["root_path"] == "/home/user/repo"

    def test_create_with_settings(self, client):
        hdr, _ = _auth_user(client)
        r = client.post("/api/projects", json={
            "name": "Configured",
            "settings": {"model": "qwen2.5-coder:7b", "rag": True},
        }, headers=hdr)
        assert r.status_code == 201
        assert r.json()["settings"]["model"] == "qwen2.5-coder:7b"

    def test_create_empty_name_rejected(self, client):
        hdr, _ = _auth_user(client)
        r = client.post("/api/projects", json={"name": ""}, headers=hdr)
        assert r.status_code == 422

    def test_create_missing_name(self, client):
        hdr, _ = _auth_user(client)
        r = client.post("/api/projects", json={"description": "no name"}, headers=hdr)
        assert r.status_code == 422

    def test_create_requires_auth(self, client):
        r = client.post("/api/projects", json={"name": "X"})
        assert r.status_code == 401


class TestProjectList:
    def test_list_empty(self, client):
        hdr, _ = _auth_user(client)
        r = client.get("/api/projects", headers=hdr)
        assert r.status_code == 200
        assert r.json() == []

    def test_list_multiple(self, client):
        hdr, _ = _auth_user(client)
        for i in range(3):
            client.post("/api/projects", json={"name": f"P{i}"}, headers=hdr)
        r = client.get("/api/projects", headers=hdr)
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_list_requires_auth(self, client):
        r = client.get("/api/projects")
        assert r.status_code == 401


class TestProjectGet:
    def test_get_success(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={"name": "P1"}, headers=hdr).json()["id"]
        r = client.get(f"/api/projects/{pid}", headers=hdr)
        assert r.status_code == 200
        assert r.json()["name"] == "P1"

    def test_get_nonexistent(self, client):
        hdr, _ = _auth_user(client)
        r = client.get("/api/projects/nonexistent-id", headers=hdr)
        assert r.status_code == 404

    def test_get_requires_auth(self, client):
        r = client.get("/api/projects/some-id")
        assert r.status_code == 401


class TestProjectUpdate:
    def test_update_name(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={"name": "Old"}, headers=hdr).json()["id"]
        r = client.patch(f"/api/projects/{pid}", json={"name": "New"}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["name"] == "New"

    def test_update_description(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={"name": "P"}, headers=hdr).json()["id"]
        r = client.patch(f"/api/projects/{pid}", json={"description": "Updated desc"}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["description"] == "Updated desc"

    def test_update_settings(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={"name": "P"}, headers=hdr).json()["id"]
        r = client.patch(f"/api/projects/{pid}", json={"settings": {"k": "v"}}, headers=hdr)
        assert r.status_code == 200
        assert r.json()["settings"] == {"k": "v"}

    def test_update_partial_unchanged_fields(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={
            "name": "P", "description": "keep me",
        }, headers=hdr).json()["id"]
        client.patch(f"/api/projects/{pid}", json={"name": "Changed"}, headers=hdr)
        r = client.get(f"/api/projects/{pid}", headers=hdr)
        assert r.json()["name"] == "Changed"
        assert r.json()["description"] == "keep me"

    def test_update_nonexistent(self, client):
        hdr, _ = _auth_user(client)
        r = client.patch("/api/projects/nope", json={"name": "X"}, headers=hdr)
        assert r.status_code == 404

    def test_update_requires_auth(self, client):
        r = client.patch("/api/projects/x", json={"name": "X"})
        assert r.status_code == 401


class TestProjectDelete:
    def test_delete_success(self, client):
        hdr, _ = _auth_user(client)
        pid = client.post("/api/projects", json={"name": "P"}, headers=hdr).json()["id"]
        r = client.delete(f"/api/projects/{pid}", headers=hdr)
        assert r.status_code == 204
        assert client.get(f"/api/projects/{pid}", headers=hdr).status_code == 404

    def test_delete_nonexistent(self, client):
        hdr, _ = _auth_user(client)
        r = client.delete("/api/projects/nope", headers=hdr)
        assert r.status_code == 404

    def test_delete_requires_auth(self, client):
        r = client.delete("/api/projects/x")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  Ownership enforcement
# ═══════════════════════════════════════════════════════════════════════════════

class TestOwnership:
    def test_user_cannot_see_others_projects(self, client):
        hdr_a, _ = _auth_user(client, "alice@f.dev", "alice")
        hdr_b, _ = _auth_user(client, "bob@f.dev", "bob")

        pid_a = client.post("/api/projects", json={"name": "Alice's"}, headers=hdr_a).json()["id"]

        # Bob can't see Alice's project
        r = client.get(f"/api/projects/{pid_a}", headers=hdr_b)
        assert r.status_code == 404

        # Bob's list is empty
        r = client.get("/api/projects", headers=hdr_b)
        assert r.json() == []

    def test_user_cannot_update_others_projects(self, client):
        hdr_a, _ = _auth_user(client, "alice@f.dev", "alice")
        hdr_b, _ = _auth_user(client, "bob@f.dev", "bob")

        pid_a = client.post("/api/projects", json={"name": "Alice's"}, headers=hdr_a).json()["id"]

        r = client.patch(f"/api/projects/{pid_a}", json={"name": "Hacked"}, headers=hdr_b)
        assert r.status_code == 404

        # Alice's project is unchanged
        r = client.get(f"/api/projects/{pid_a}", headers=hdr_a)
        assert r.json()["name"] == "Alice's"

    def test_user_cannot_delete_others_projects(self, client):
        hdr_a, _ = _auth_user(client, "alice@f.dev", "alice")
        hdr_b, _ = _auth_user(client, "bob@f.dev", "bob")

        pid_a = client.post("/api/projects", json={"name": "Alice's"}, headers=hdr_a).json()["id"]

        r = client.delete(f"/api/projects/{pid_a}", headers=hdr_b)
        assert r.status_code == 404

        # Project still exists for Alice
        assert client.get(f"/api/projects/{pid_a}", headers=hdr_a).status_code == 200

    def test_two_users_separate_lists(self, client):
        hdr_a, _ = _auth_user(client, "alice@f.dev", "alice")
        hdr_b, _ = _auth_user(client, "bob@f.dev", "bob")

        client.post("/api/projects", json={"name": "A1"}, headers=hdr_a)
        client.post("/api/projects", json={"name": "B1"}, headers=hdr_b)
        client.post("/api/projects", json={"name": "B2"}, headers=hdr_b)

        assert len(client.get("/api/projects", headers=hdr_a).json()) == 1
        assert len(client.get("/api/projects", headers=hdr_b).json()) == 2
