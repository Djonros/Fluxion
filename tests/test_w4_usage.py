"""Tests for Phase W4 — rate limiting + usage tracking.

Covers:
- RateLimiter sliding window (unit level)
- Tier classification
- 429 after exceeding limits (auth tier, crud tier)
- Usage logging middleware records requests
- Usage summary endpoint
- Usage breakdown endpoint (per-endpoint stats)
- User isolation of stats
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.database import Base, get_db
from server.usage.limiter import RateLimiter, _classify
from server.usage.models import ApiUsageLog


# ═══════════════════════════════════════════════════════════════════════════════
#  RateLimiter unit tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiterUnit:
    def test_allows_under_limit(self):
        rl = RateLimiter(limits={"default": 5})
        for _ in range(5):
            rl.check("u1", "default")

    def test_blocks_over_limit(self):
        import fastapi
        rl = RateLimiter(limits={"default": 3})
        for _ in range(3):
            rl.check("u1", "default")
        with pytest.raises(fastapi.HTTPException) as exc:
            rl.check("u1", "default")
        assert exc.value.status_code == 429

    def test_independent_users(self):
        import fastapi
        rl = RateLimiter(limits={"default": 2})
        rl.check("alice", "default")
        rl.check("alice", "default")
        # bob unaffected
        rl.check("bob", "default")
        with pytest.raises(fastapi.HTTPException):
            rl.check("alice", "default")

    def test_independent_tiers(self):
        rl = RateLimiter(limits={"auth": 2, "chat": 100})
        rl.check("u1", "auth")
        rl.check("u1", "auth")
        # different tier unaffected
        rl.check("u1", "chat")

    def test_remaining(self):
        rl = RateLimiter(limits={"default": 3})
        assert rl.remaining("u1", "default") == 3
        rl.check("u1", "default")
        assert rl.remaining("u1", "default") == 2

    def test_reset(self):
        import fastapi
        rl = RateLimiter(limits={"default": 1})
        rl.check("u1", "default")
        rl.reset()
        rl.check("u1", "default")  # no exception

    def test_unknown_tier_uses_default(self):
        rl = RateLimiter(limits={"default": 5})
        rl.check("u1", "nonexistent_tier")  # falls back


class TestTierClassification:
    def test_auth(self):
        assert _classify("/api/auth/login") == "auth"
        assert _classify("/api/auth/register") == "auth"

    def test_chat(self):
        assert _classify("/api/chat") == "chat"
        assert _classify("/api/agent/run") == "chat"
        assert _classify("/api/sessions/abc/chat") == "chat"

    def test_usage(self):
        assert _classify("/api/usage") == "usage"
        assert _classify("/api/usage/breakdown") == "usage"

    def test_crud(self):
        assert _classify("/api/projects") == "crud"
        assert _classify("/api/sessions") == "crud"
        assert _classify("/api/sessions/abc") == "crud"

    def test_default(self):
        assert _classify("/api/health") == "default"


# ═══════════════════════════════════════════════════════════════════════════════
#  Fixtures for integration tests
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
def client(mock_settings, tmp_path):
    """TestClient with isolated DB and strict rate limits for testing."""
    from server.usage.limiter import set_limiter

    db_path = tmp_path / "test_w4.db"
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
        # Tight limits AFTER create_app (which resets the global limiter)
        test_limiter = RateLimiter(limits={
            "auth": 5, "chat": 5, "crud": 5, "usage": 100, "default": 60,
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


# ═══════════════════════════════════════════════════════════════════════════════
#  Rate limiting integration
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimitIntegration:
    def test_login_rate_limited(self, client):
        """Auth tier: 5/min. 1 register + 5 logins → 6th login is 429."""
        _auth(client)
        codes = []
        for _ in range(6):
            r = client.post("/api/auth/login", json={
                "email": "alice@fluxion.dev", "password": "wrong",
            })
            codes.append(r.status_code)
        assert 429 in codes
        assert codes[-1] == 429

    def test_429_has_retry_after_header(self, client):
        _auth(client)
        for _ in range(5):
            client.post("/api/auth/login", json={
                "email": "alice@fluxion.dev", "password": "wrong",
            })
        r = client.post("/api/auth/login", json={
            "email": "alice@fluxion.dev", "password": "wrong",
        })
        assert r.status_code == 429
        assert "retry-after" in {k.lower() for k in r.headers}

    def test_crud_rate_limited(self, client):
        """Crud tier: 5/min. 6th project create is 429."""
        hdr = _auth(client)
        codes = []
        for i in range(6):
            r = client.post("/api/projects", json={"name": f"P{i}"}, headers=hdr)
            codes.append(r.status_code)
        assert 429 in codes

    def test_users_have_independent_limits(self, client):
        """Alice exhausting crud tier does not affect Bob."""
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobxx")
        for i in range(6):
            client.post("/api/projects", json={"name": f"A{i}"}, headers=hdr_a)
        # Bob still fine
        r = client.post("/api/projects", json={"name": "B"}, headers=hdr_b)
        assert r.status_code == 201


# ═══════════════════════════════════════════════════════════════════════════════
#  Usage logging
# ═══════════════════════════════════════════════════════════════════════════════

class TestUsageLogging:
    def test_requests_are_logged(self, client):
        hdr = _auth(client)
        client.post("/api/projects", json={"name": "P1"}, headers=hdr)
        client.get("/api/projects", headers=hdr)

        db = client._session()
        logs = db.query(ApiUsageLog).all()
        db.close()
        endpoints = [l.endpoint for l in logs]
        assert "/api/auth/register" in endpoints
        assert "/api/projects" in endpoints

    def test_log_fields(self, client):
        hdr = _auth(client)
        client.post("/api/projects", json={"name": "P1"}, headers=hdr)

        db = client._session()
        log = db.query(ApiUsageLog).filter(
            ApiUsageLog.endpoint == "/api/projects",
        ).first()
        db.close()
        assert log is not None
        assert log.method == "POST"
        assert log.status_code == 201
        assert log.latency_ms >= 0
        assert log.user_id is not None

    def test_errors_are_logged(self, client):
        hdr = _auth(client)
        client.get("/api/projects/nonexistent", headers=hdr)

        db = client._session()
        log = db.query(ApiUsageLog).filter(
            ApiUsageLog.endpoint == "/api/projects/nonexistent",
        ).first()
        db.close()
        assert log.status_code == 404

    def test_401_logged_without_user(self, client):
        client.get("/api/projects")  # no auth

        db = client._session()
        log = db.query(ApiUsageLog).filter(
            ApiUsageLog.endpoint == "/api/projects",
        ).first()
        db.close()
        assert log is not None
        assert log.status_code == 401
        assert log.user_id is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Usage stats endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestUsageSummary:
    def test_summary_counts_requests(self, client):
        hdr = _auth(client)
        client.post("/api/projects", json={"name": "P1"}, headers=hdr)
        client.get("/api/projects", headers=hdr)

        r = client.get("/api/usage", headers=hdr)
        assert r.status_code == 200
        data = r.json()
        # register + create + list + this usage call itself may lag one behind
        assert data["total_requests"] >= 2
        assert data["total_errors"] == 0
        assert data["error_rate"] == 0.0
        assert data["avg_latency_ms"] >= 0
        assert data["period_hours"] == 24

    def test_summary_counts_errors(self, client):
        hdr = _auth(client)
        client.get("/api/projects/nope", headers=hdr)

        r = client.get("/api/usage", headers=hdr)
        data = r.json()
        assert data["total_errors"] >= 1
        assert data["error_rate"] > 0

    def test_summary_hours_param(self, client):
        hdr = _auth(client)
        r = client.get("/api/usage?hours=1", headers=hdr)
        assert r.json()["period_hours"] == 1

    def test_summary_requires_auth(self, client):
        r = client.get("/api/usage")
        assert r.status_code == 401

    def test_summary_isolated_per_user(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobxx")

        client.post("/api/projects", json={"name": "A"}, headers=hdr_a)
        client.post("/api/projects", json={"name": "B1"}, headers=hdr_b)
        client.post("/api/projects", json={"name": "B2"}, headers=hdr_b)

        summary_a = client.get("/api/usage", headers=hdr_a).json()
        summary_b = client.get("/api/usage", headers=hdr_b).json()
        assert summary_b["total_requests"] > summary_a["total_requests"]


class TestUsageBreakdown:
    def test_breakdown_lists_endpoints(self, client):
        hdr = _auth(client)
        client.post("/api/projects", json={"name": "P1"}, headers=hdr)
        client.post("/api/projects", json={"name": "P2"}, headers=hdr)
        client.get("/api/projects", headers=hdr)

        r = client.get("/api/usage/breakdown", headers=hdr)
        assert r.status_code == 200
        endpoints = {e["endpoint"] for e in r.json()["endpoints"]}
        assert "/api/projects" in endpoints

    def test_breakdown_counts(self, client):
        hdr = _auth(client)
        client.post("/api/projects", json={"name": "P1"}, headers=hdr)
        client.post("/api/projects", json={"name": "P2"}, headers=hdr)

        r = client.get("/api/usage/breakdown", headers=hdr)
        proj = next(
            e for e in r.json()["endpoints"] if e["endpoint"] == "/api/projects"
        )
        assert proj["method"] == "POST"
        assert proj["count"] == 2
        assert proj["errors"] == 0

    def test_breakdown_errors_counted(self, client):
        hdr = _auth(client)
        client.get("/api/projects/nope", headers=hdr)

        r = client.get("/api/usage/breakdown", headers=hdr)
        failed = next(
            e for e in r.json()["endpoints"] if e["endpoint"] == "/api/projects/nope"
        )
        assert failed["errors"] == 1

    def test_breakdown_requires_auth(self, client):
        r = client.get("/api/usage/breakdown")
        assert r.status_code == 401
