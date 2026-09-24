"""Tests for Phase 13.3 — Enterprise tier: organizations, SSO-lite, limits.

Covers:
- Organization CRUD: create (owner auto-member), list, get, isolation (404)
- Duplicate / invalid email domain validation
- SSO-lite: auto-join on register via matching email domain
- Member management: list, remove (owner-only, owner protected)
- Enterprise plan in public catalog
- resolve_user_limits: enterprise inheritance from org owner's subscription
- resolve_user_limits: per-tier max when member also has a personal plan
- Org usage analytics: owner-only, totals + per-member aggregation, time filter
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.auth.models import User
from server.billing.models import Subscription
from server.billing.plans import PLANS
from server.billing.service import resolve_user_limits, set_session_factory
from server.database import Base, get_db
from server.organizations.models import Organization, OrgMember
from server.usage.limiter import RateLimiter, _classify, set_limiter
from server.usage.models import ApiUsageLog


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
def client(mock_settings, tmp_path):
    """TestClient with isolated DB."""
    from server.billing.service import resolve_user_limits

    db_path = tmp_path / "test_orgs.db"
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
        set_session_factory(TestSession)
        test_limiter = RateLimiter()
        test_limiter.set_limit_resolver(resolve_user_limits)
        set_limiter(test_limiter)
        app.state.rate_limiter = test_limiter
        c = TestClient(app)
        c._session = TestSession
        yield c
    set_session_factory(None)
    set_limiter(RateLimiter())  # restore defaults


def _auth(client, email="owner@acme.com", username="owner"):
    r = client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "SuperSecret123",
    })
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user_id(client, email: str) -> str:
    db = client._session()
    user = db.query(User).filter(User.email == email).first()
    db.close()
    assert user is not None
    return user.id


def _make_org(client, headers, name="Acme Corp", domain="acme.com"):
    r = client.post("/api/organizations", json={"name": name, "email_domain": domain},
                    headers=headers)
    assert r.status_code == 201
    return r.json()


def _set_subscription(client, user_id: str, plan_id: str, status_: str = "active"):
    db = client._session()
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is None:
        sub = Subscription(user_id=user_id, plan_id=plan_id, status=status_)
        db.add(sub)
    else:
        sub.plan_id = plan_id
        sub.status = status_
    db.commit()
    db.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Organization CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestOrgCrud:
    def test_requires_auth(self, client):
        assert client.get("/api/organizations").status_code == 401

    def test_create_makes_owner_member(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        assert org["name"] == "Acme Corp"
        assert org["email_domain"] == "acme.com"
        assert org["member_count"] == 1

        db = client._session()
        member = db.query(OrgMember).filter(OrgMember.org_id == org["id"]).one()
        db.close()
        assert member.role == "owner"
        assert member.user_id == org["owner_id"]

    def test_domain_normalized(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr, domain="@ACME.com")
        assert org["email_domain"] == "acme.com"

    def test_duplicate_domain_409(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        hdr2 = _auth(client, "owner2@other.io", "owner2")
        r = client.post("/api/organizations", json={
            "name": "Copycat", "email_domain": "acme.com",
        }, headers=hdr2)
        assert r.status_code == 409

    def test_invalid_domain_422(self, client):
        hdr = _auth(client)
        r = client.post("/api/organizations", json={
            "name": "Bad", "email_domain": "nodot",
        }, headers=hdr)
        assert r.status_code == 422

    def test_list_and_get(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        _make_org(client, hdr, name="Beta", domain="beta.io")

        r = client.get("/api/organizations", headers=hdr)
        assert r.status_code == 200
        assert len(r.json()) == 2

        r = client.get(f"/api/organizations/{org['id']}", headers=hdr)
        assert r.status_code == 200
        assert r.json()["id"] == org["id"]

    def test_non_member_gets_404(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        hdr_outsider = _auth(client, "x@other.io", "xyz")
        assert client.get(f"/api/organizations/{org['id']}",
                          headers=hdr_outsider).status_code == 404

    def test_orgs_classified_as_crud(self):
        assert _classify("/api/organizations") == "crud"
        assert _classify("/api/organizations/xyz/usage") == "crud"


# ═══════════════════════════════════════════════════════════════════════════════
#  SSO-lite auto-join + members
# ═══════════════════════════════════════════════════════════════════════════════

class TestSsoAutoJoin:
    def test_register_auto_joins_matching_domain(self, client):
        hdr = _auth(client)  # owner@acme.com
        org = _make_org(client, hdr)

        _auth(client, "newhire@acme.com", "newhire")
        r = client.get(f"/api/organizations/{org['id']}/members", headers=hdr)
        assert r.status_code == 200
        members = r.json()
        assert len(members) == 2
        newhire = next(m for m in members if m["email"] == "newhire@acme.com")
        assert newhire["role"] == "member"

    def test_other_domain_not_joined(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)

        _auth(client, "stranger@elsewhere.io", "stranger")
        r = client.get(f"/api/organizations/{org['id']}/members", headers=hdr)
        assert len(r.json()) == 1

    def test_new_member_sees_org(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        hdr_new = _auth(client, "dev@acme.com", "dev")

        r = client.get("/api/organizations", headers=hdr_new)
        assert r.status_code == 200
        assert [o["id"] for o in r.json()] == [org["id"]]
        assert r.json()[0]["member_count"] == 2


class TestMemberManagement:
    def test_remove_member_by_owner(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")

        r = client.delete(f"/api/organizations/{org['id']}/members/{dev_id}", headers=hdr)
        assert r.status_code == 204
        r = client.get(f"/api/organizations/{org['id']}/members", headers=hdr)
        assert len(r.json()) == 1

    def test_non_owner_cannot_remove(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        hdr_dev = _auth(client, "dev@acme.com", "dev")
        owner_id = org["owner_id"]

        r = client.delete(f"/api/organizations/{org['id']}/members/{owner_id}",
                          headers=hdr_dev)
        assert r.status_code == 403

    def test_owner_cannot_remove_self(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        r = client.delete(f"/api/organizations/{org['id']}/members/{org['owner_id']}",
                          headers=hdr)
        assert r.status_code == 400

    def test_remove_unknown_member_404(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        r = client.delete(f"/api/organizations/{org['id']}/members/nonexistent",
                          headers=hdr)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
#  Enterprise plan + limit inheritance
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnterprisePlan:
    def test_plans_catalog_includes_enterprise(self, client):
        r = client.get("/api/billing/plans")
        assert r.status_code == 200
        plans = {p["id"]: p for p in r.json()["plans"]}
        assert "enterprise" in plans
        ent = plans["enterprise"]
        assert ent["price_monthly_usd"] > plans["team"]["price_monthly_usd"]
        assert ent["limits"]["chat"] > plans["team"]["limits"]["chat"]

    def test_enterprise_dominates_team_limits(self):
        for tier, limit in PLANS["enterprise"].limits.items():
            assert limit >= PLANS["team"].limits[tier]


class TestLimitInheritance:
    def test_member_inherits_enterprise_limits(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        _set_subscription(client, _user_id(client, "owner@acme.com"), "enterprise")
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")

        assert resolve_user_limits(dev_id) == dict(PLANS["enterprise"].limits)

    def test_inactive_owner_sub_no_inheritance(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        _set_subscription(client, _user_id(client, "owner@acme.com"), "enterprise", "past_due")
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")

        assert resolve_user_limits(dev_id) is None

    def test_owner_without_paid_sub_no_inheritance(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")

        assert resolve_user_limits(dev_id) is None

    def test_personal_pro_plus_org_enterprise_takes_max(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        _set_subscription(client, _user_id(client, "owner@acme.com"), "enterprise")
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")
        _set_subscription(client, dev_id, "pro")

        assert resolve_user_limits(dev_id) == dict(PLANS["enterprise"].limits)

    def test_non_org_user_unaffected(self, client):
        hdr = _auth(client)
        _make_org(client, hdr)
        _set_subscription(client, _user_id(client, "owner@acme.com"), "enterprise")
        _auth(client, "stranger@elsewhere.io", "stranger")
        stranger_id = _user_id(client, "stranger@elsewhere.io")

        assert resolve_user_limits(stranger_id) is None


# ═══════════════════════════════════════════════════════════════════════════════
#  Org usage analytics
# ═══════════════════════════════════════════════════════════════════════════════

def _clear_logs(client):
    """Drop middleware-generated usage logs so counts are deterministic."""
    db = client._session()
    db.query(ApiUsageLog).delete()
    db.commit()
    db.close()


def _log(client, user_id: str, endpoint: str, status_code: int = 200,
         latency_ms: int = 50, tokens_in: int = 10, tokens_out: int = 20,
         age_hours: float = 1.0):
    db = client._session()
    db.add(ApiUsageLog(
        user_id=user_id, endpoint=endpoint, method="POST",
        status_code=status_code, latency_ms=latency_ms,
        tokens_in=tokens_in, tokens_out=tokens_out,
        created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
    ))
    db.commit()
    db.close()


class TestOrgUsage:
    def test_owner_only(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        hdr_dev = _auth(client, "dev@acme.com", "dev")

        r = client.get(f"/api/organizations/{org['id']}/usage", headers=hdr_dev)
        assert r.status_code == 403
        assert client.get(f"/api/organizations/{org['id']}/usage",
                          headers=hdr).status_code == 200

    def test_aggregates_across_members(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        owner_id = org["owner_id"]
        _auth(client, "dev@acme.com", "dev")
        dev_id = _user_id(client, "dev@acme.com")

        _clear_logs(client)
        _log(client, owner_id, "/api/chat", status_code=200, latency_ms=100,
             tokens_in=10, tokens_out=30)
        _log(client, owner_id, "/api/projects", status_code=500, latency_ms=40,
             tokens_in=0, tokens_out=0)
        _log(client, dev_id, "/api/chat", status_code=200, latency_ms=200,
             tokens_in=20, tokens_out=40)

        r = client.get(f"/api/organizations/{org['id']}/usage?hours=24", headers=hdr)
        assert r.status_code == 200
        data = r.json()
        assert data["org_id"] == org["id"]
        assert data["member_count"] == 2
        assert data["total_requests"] == 3
        assert data["total_errors"] == 1
        assert data["error_rate"] == round(1 / 3, 4)
        assert data["avg_latency_ms"] == round((100 + 40 + 200) / 3, 1)
        assert data["tokens_in"] == 30
        assert data["tokens_out"] == 70

        by_email = {m["email"]: m for m in data["members"]}
        assert by_email["owner@acme.com"]["requests"] == 2
        assert by_email["owner@acme.com"]["errors"] == 1
        assert by_email["dev@acme.com"]["requests"] == 1
        assert by_email["dev@acme.com"]["tokens_out"] == 40

    def test_hours_filter_excludes_old_logs(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)

        _clear_logs(client)
        _log(client, org["owner_id"], "/api/chat", age_hours=1.0)
        _log(client, org["owner_id"], "/api/chat", age_hours=48.0)

        r = client.get(f"/api/organizations/{org['id']}/usage?hours=2", headers=hdr)
        assert r.json()["total_requests"] == 1

    def test_empty_org_zeroed(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        _clear_logs(client)
        r = client.get(f"/api/organizations/{org['id']}/usage", headers=hdr)
        data = r.json()
        assert data["total_requests"] == 0
        assert data["error_rate"] == 0.0
        assert data["members"][0]["requests"] == 0

    def test_non_member_404(self, client):
        hdr = _auth(client)
        org = _make_org(client, hdr)
        hdr_out = _auth(client, "stranger@elsewhere.io", "stranger")
        assert client.get(f"/api/organizations/{org['id']}/usage",
                          headers=hdr_out).status_code == 404
