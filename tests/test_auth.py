"""Tests for the Fluxion authentication system.

Tests cover: registration, login, token refresh, /me profile,
duplicate detection, password validation, invalid tokens, and
disabled accounts.  Each test gets a fresh isolated SQLite database.
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
    """Minimal Settings mock — same shape as test_server.py."""
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
def auth_client(mock_settings, tmp_path):
    """TestClient with an isolated SQLite DB for auth tests."""
    db_path = tmp_path / "test_auth.db"
    test_engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    TestSessionLocal = sessionmaker(
        autocommit=False, autoflush=False, bind=test_engine,
    )
    Base.metadata.create_all(bind=test_engine)

    def _override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    with patch("server.app.OllamaBackend") as mock_backend_cls, \
         patch("server.app.WebSearch") as mock_ws_cls:
        mock_backend = MagicMock()
        mock_backend.is_available.return_value = True
        mock_backend_cls.return_value = mock_backend
        mock_ws_cls.from_settings.return_value = MagicMock()

        from server.app import create_app
        app = create_app(mock_settings)
        app.dependency_overrides[get_db] = _override_get_db
        client = TestClient(app)
        client._test_engine = test_engine
        client._test_session = TestSessionLocal
        return client


def _register(client, email="user@fluxion.dev", username="testuser", password="SuperSecret123"):
    """Helper: register a user and return the response."""
    return client.post("/api/auth/register", json={
        "email": email,
        "username": username,
        "password": password,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/auth/register
# ═══════════════════════════════════════════════════════════════════════════════

class TestRegister:
    def test_register_success(self, auth_client):
        r = _register(auth_client)
        assert r.status_code == 201
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data
        assert data["token_type"] == "bearer"

    def test_register_returns_valid_jwt(self, auth_client):
        """Tokens should have three dot-separated base64 parts."""
        r = _register(auth_client)
        access = r.json()["access_token"]
        refresh = r.json()["refresh_token"]
        assert access.count(".") == 2
        assert refresh.count(".") == 2
        assert access != refresh

    def test_register_duplicate_email(self, auth_client):
        _register(auth_client, email="dup@fluxion.dev")
        r = _register(auth_client, email="dup@fluxion.dev", username="other")
        assert r.status_code == 409
        assert "email" in r.json()["detail"].lower()

    def test_register_duplicate_username(self, auth_client):
        _register(auth_client, username="samename")
        r = _register(auth_client, email="other@fluxion.dev", username="samename")
        assert r.status_code == 409
        assert "username" in r.json()["detail"].lower()

    def test_register_short_password(self, auth_client):
        r = _register(auth_client, password="short")
        assert r.status_code == 422

    def test_register_invalid_email(self, auth_client):
        r = auth_client.post("/api/auth/register", json={
            "email": "not-an-email",
            "username": "testuser",
            "password": "SuperSecret123",
        })
        assert r.status_code == 422

    def test_register_missing_fields(self, auth_client):
        r = auth_client.post("/api/auth/register", json={"email": "a@b.com"})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/auth/login
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogin:
    def test_login_success(self, auth_client):
        _register(auth_client)
        r = auth_client.post("/api/auth/login", json={
            "email": "user@fluxion.dev",
            "password": "SuperSecret123",
        })
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data
        assert "refresh_token" in data

    def test_login_wrong_password(self, auth_client):
        _register(auth_client)
        r = auth_client.post("/api/auth/login", json={
            "email": "user@fluxion.dev",
            "password": "WrongPassword99",
        })
        assert r.status_code == 401

    def test_login_nonexistent_user(self, auth_client):
        r = auth_client.post("/api/auth/login", json={
            "email": "nobody@fluxion.dev",
            "password": "SuperSecret123",
        })
        assert r.status_code == 401

    def test_login_tokens_differ_from_register(self, auth_client):
        reg = _register(auth_client).json()
        login = auth_client.post("/api/auth/login", json={
            "email": "user@fluxion.dev",
            "password": "SuperSecret123",
        }).json()
        assert reg["access_token"] != login["access_token"]


# ═══════════════════════════════════════════════════════════════════════════════
#  POST /api/auth/refresh
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefresh:
    def test_refresh_success(self, auth_client):
        tokens = _register(auth_client).json()
        r = auth_client.post("/api/auth/refresh", json={
            "refresh_token": tokens["refresh_token"],
        })
        assert r.status_code == 200
        new_tokens = r.json()
        assert new_tokens["access_token"] != tokens["access_token"]

    def test_refresh_with_access_token_fails(self, auth_client):
        """Access tokens must not work for refresh."""
        tokens = _register(auth_client).json()
        r = auth_client.post("/api/auth/refresh", json={
            "refresh_token": tokens["access_token"],
        })
        assert r.status_code == 401

    def test_refresh_with_garbage_token(self, auth_client):
        r = auth_client.post("/api/auth/refresh", json={
            "refresh_token": "not.a.real.token",
        })
        assert r.status_code == 401

    def test_refresh_missing_field(self, auth_client):
        r = auth_client.post("/api/auth/refresh", json={})
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
#  GET /api/auth/me
# ═══════════════════════════════════════════════════════════════════════════════

class TestMe:
    def test_me_success(self, auth_client):
        tokens = _register(auth_client).json()
        r = auth_client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {tokens['access_token']}",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == "user@fluxion.dev"
        assert data["username"] == "testuser"
        assert data["is_active"] is True
        assert data["is_admin"] is False
        assert "hashed_password" not in data
        assert "id" in data

    def test_me_no_token(self, auth_client):
        r = auth_client.get("/api/auth/me")
        assert r.status_code == 401

    def test_me_invalid_token(self, auth_client):
        r = auth_client.get("/api/auth/me", headers={
            "Authorization": "Bearer garbage.token.here",
        })
        assert r.status_code == 401

    def test_me_refresh_token_rejected(self, auth_client):
        """Accessing /me with a refresh token should fail (wrong type)."""
        tokens = _register(auth_client).json()
        r = auth_client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {tokens['refresh_token']}",
        })
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  Disabled user
# ═══════════════════════════════════════════════════════════════════════════════

class TestDisabledUser:
    def test_login_disabled_user(self, auth_client):
        tokens = _register(auth_client).json()

        # Disable the user directly in the DB
        from server.auth.models import User
        db = auth_client._test_session()
        user = db.query(User).filter(User.email == "user@fluxion.dev").first()
        user.is_active = False
        db.commit()
        db.close()

        r = auth_client.post("/api/auth/login", json={
            "email": "user@fluxion.dev",
            "password": "SuperSecret123",
        })
        assert r.status_code == 403

    def test_me_disabled_user(self, auth_client):
        tokens = _register(auth_client).json()

        from server.auth.models import User
        db = auth_client._test_session()
        user = db.query(User).filter(User.email == "user@fluxion.dev").first()
        user.is_active = False
        db.commit()
        db.close()

        r = auth_client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {tokens['access_token']}",
        })
        assert r.status_code == 403
