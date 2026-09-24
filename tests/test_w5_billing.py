"""Tests for Phase W5 — billing: plans, subscriptions, Stripe checkout + webhook.

Covers:
- Plan catalog endpoint (public)
- Default subscription auto-creation (free)
- Checkout endpoint (validation, not-configured 503, mocked Stripe flow)
- Webhook signature verification (valid / invalid / missing secret)
- checkout.session.completed → plan activation
- customer.subscription.updated / deleted → status changes + downgrade
- invoice.payment_failed → past_due
- Paid plan raises rate limits via the limiter resolver
- Cancel endpoint (Stripe + manual dev downgrade)
- User isolation
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.auth.models import User
from server.billing.models import Subscription
from server.billing.plans import PLANS
from server.billing.service import set_session_factory
from server.database import Base, get_db
from server.usage.limiter import RateLimiter, _classify, set_limiter

WEBHOOK_SECRET = "whsec_test_secret"


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def _sign(payload: bytes, secret: str = WEBHOOK_SECRET) -> str:
    """Build a valid Stripe-Signature header for *payload*."""
    t = int(time.time())
    mac = hmac.new(secret.encode(), f"{t}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={t},v1={mac}"


def _event_payload(etype: str, obj: dict) -> bytes:
    return json.dumps({
        "id": "evt_test_1",
        "object": "event",
        "api_version": "2024-06-20",
        "type": etype,
        "data": {"object": obj},
    }).encode()


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_PRICE_PRO", raising=False)


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
    """TestClient with isolated DB and tight crud limits (5/min)."""
    from server.billing.service import resolve_user_limits

    db_path = tmp_path / "test_w5.db"
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
        # Tight crud limit AFTER create_app (which resets the global limiter)
        test_limiter = RateLimiter(limits={
            "auth": 30, "chat": 30, "crud": 5, "usage": 100, "billing": 30,
            "default": 60,
        })
        test_limiter.set_limit_resolver(resolve_user_limits)
        set_limiter(test_limiter)
        app.state.rate_limiter = test_limiter
        c = TestClient(app)
        c._session = TestSession
        yield c
    set_session_factory(None)
    set_limiter(RateLimiter())  # restore defaults


def _auth(client, email="alice@fluxion.dev", username="alice"):
    r = client.post("/api/auth/register", json={
        "email": email, "username": username, "password": "SuperSecret123",
    })
    assert r.status_code == 201
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _user_id(client, email="alice@fluxion.dev") -> str:
    db = client._session()
    user = db.query(User).filter(User.email == email).first()
    db.close()
    assert user is not None
    return user.id


def _webhook(client, etype: str, obj: dict, valid: bool = True):
    payload = _event_payload(etype, obj)
    sig = _sign(payload) if valid else "t=1,v1=deadbeef"
    return client.post(
        "/api/billing/webhook", content=payload,
        headers={"Stripe-Signature": sig},
    )


def _activate_pro(client, user_id: str):
    r = _webhook(client, "checkout.session.completed", {
        "id": "cs_test_1",
        "customer": "cus_test_1",
        "subscription": "sub_test_1",
        "metadata": {"user_id": user_id, "plan_id": "pro"},
    })
    assert r.status_code == 200
    assert r.json()["handled"] == "checkout.session.completed"


# ═══════════════════════════════════════════════════════════════════════════════
#  Plans
# ═══════════════════════════════════════════════════════════════════════════════

class TestPlans:
    def test_plans_public(self, client):
        r = client.get("/api/billing/plans")
        assert r.status_code == 200
        plans = {p["id"]: p for p in r.json()["plans"]}
        assert set(plans) == {"free", "pro", "team", "enterprise"}
        assert plans["free"]["price_monthly_usd"] == 0
        assert plans["pro"]["price_monthly_usd"] > 0
        assert plans["team"]["limits"]["chat"] > plans["free"]["limits"]["chat"]

    def test_billing_tier_classification(self):
        assert _classify("/api/billing/plans") == "billing"
        assert _classify("/api/billing/subscription") == "billing"
        assert _classify("/api/billing/webhook") == "billing"


# ═══════════════════════════════════════════════════════════════════════════════
#  Subscription
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubscription:
    def test_defaults_to_free(self, client):
        hdr = _auth(client)
        r = client.get("/api/billing/subscription", headers=hdr)
        assert r.status_code == 200
        data = r.json()
        assert data["plan_id"] == "free"
        assert data["plan_name"] == "Free"
        assert data["status"] == "active"
        assert data["limits"] == PLANS["free"].limits
        assert data["stripe_subscription_id"] is None

    def test_row_created_in_db(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        client.get("/api/billing/subscription", headers=hdr)

        db = client._session()
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        db.close()
        assert sub is not None
        assert sub.plan_id == "free"

    def test_requires_auth(self, client):
        r = client.get("/api/billing/subscription")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  Checkout
# ═══════════════════════════════════════════════════════════════════════════════

class TestCheckout:
    def test_unknown_plan_404(self, client):
        hdr = _auth(client)
        r = client.post("/api/billing/checkout", json={
            "plan_id": "nonexistent", "success_url": "http://x/s", "cancel_url": "http://x/c",
        }, headers=hdr)
        assert r.status_code == 404

    def test_free_plan_400(self, client):
        hdr = _auth(client)
        r = client.post("/api/billing/checkout", json={
            "plan_id": "free", "success_url": "http://x/s", "cancel_url": "http://x/c",
        }, headers=hdr)
        assert r.status_code == 400

    def test_stripe_not_configured_503(self, client):
        hdr = _auth(client)
        r = client.post("/api/billing/checkout", json={
            "plan_id": "pro", "success_url": "http://x/s", "cancel_url": "http://x/c",
        }, headers=hdr)
        assert r.status_code == 503

    def test_price_not_configured_503(self, client, monkeypatch):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        hdr = _auth(client)
        r = client.post("/api/billing/checkout", json={
            "plan_id": "pro", "success_url": "http://x/s", "cancel_url": "http://x/c",
        }, headers=hdr)
        assert r.status_code == 503
        assert "STRIPE_PRICE_PRO" in r.json()["detail"]

    def test_checkout_returns_url_and_creates_customer(
        self, client, monkeypatch,
    ):
        monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
        monkeypatch.setenv("STRIPE_PRICE_PRO", "price_fake_pro")
        hdr = _auth(client)
        user_id = _user_id(client)

        with patch("server.billing.stripe_api.ensure_customer", return_value="cus_test_9") as mc, \
             patch("server.billing.stripe_api.create_checkout_session", return_value={
                 "id": "cs_test_9", "url": "https://checkout.stripe.com/c/pay/cs_test_9",
             }) as ms:
            r = client.post("/api/billing/checkout", json={
                "plan_id": "pro", "success_url": "http://x/s", "cancel_url": "http://x/c",
            }, headers=hdr)

        assert r.status_code == 200
        data = r.json()
        assert data["checkout_url"].endswith("cs_test_9")
        assert data["plan_id"] == "pro"
        mc.assert_called_once()
        kwargs = ms.call_args.kwargs
        assert kwargs["customer_id"] == "cus_test_9"
        assert kwargs["plan"].id == "pro"
        assert kwargs["user_id"] == user_id

        db = client._session()
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        db.close()
        assert sub.stripe_customer_id == "cus_test_9"

    def test_requires_auth(self, client):
        r = client.post("/api/billing/checkout", json={
            "plan_id": "pro", "success_url": "http://x/s", "cancel_url": "http://x/c",
        })
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
#  Webhook
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookVerification:
    def test_invalid_signature_400(self, client):
        r = _webhook(client, "checkout.session.completed", {}, valid=False)
        assert r.status_code == 400
        assert "signature" in r.json()["detail"].lower()

    def test_missing_secret_503(self, client, monkeypatch):
        monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
        payload = _event_payload("checkout.session.completed", {})
        r = client.post("/api/billing/webhook", content=payload, headers={
            "Stripe-Signature": _sign(payload),
        })
        assert r.status_code == 503

    def test_unknown_event_ignored(self, client):
        r = _webhook(client, "product.created", {"id": "prod_1"})
        assert r.status_code == 200
        assert r.json() == {"received": True, "handled": None}


class TestWebhookActivation:
    def test_checkout_completed_activates_pro(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        _activate_pro(client, user_id)

        r = client.get("/api/billing/subscription", headers=hdr)
        data = r.json()
        assert data["plan_id"] == "pro"
        assert data["status"] == "active"
        assert data["stripe_customer_id"] == "cus_test_1"
        assert data["stripe_subscription_id"] == "sub_test_1"
        assert data["limits"] == PLANS["pro"].limits

    def test_pro_limits_raise_rate_cap(self, client):
        """crud tier capped at 5 by fixture; pro plan raises it to 400."""
        hdr = _auth(client)
        user_id = _user_id(client)

        codes = [client.post(
            "/api/projects", json={"name": f"P{i}"}, headers=hdr,
        ).status_code for i in range(6)]
        assert 429 in codes  # capped on free plan

        _activate_pro(client, user_id)
        r = client.post("/api/projects", json={"name": "P6"}, headers=hdr)
        assert r.status_code == 201  # pro limit applies immediately

    def test_subscription_deleted_downgrades_to_free(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        # Fill the crud window to the free cap (5)
        for i in range(5):
            assert client.post(
                "/api/projects", json={"name": f"P{i}"}, headers=hdr,
            ).status_code == 201

        _activate_pro(client, user_id)
        assert client.post(
            "/api/projects", json={"name": "P5"}, headers=hdr,
        ).status_code == 201

        r = _webhook(client, "customer.subscription.deleted", {"id": "sub_test_1"})
        assert r.status_code == 200
        assert r.json()["handled"] == "customer.subscription.deleted"

        data = client.get("/api/billing/subscription", headers=hdr).json()
        assert data["plan_id"] == "free"
        assert data["status"] == "canceled"

        r = client.post("/api/projects", json={"name": "X"}, headers=hdr)
        assert r.status_code == 429  # back to tight fixture limit

    def test_subscription_updated_status_and_period(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        _activate_pro(client, user_id)

        period_end = int(time.time()) + 30 * 24 * 3600
        r = _webhook(client, "customer.subscription.updated", {
            "id": "sub_test_1",
            "status": "past_due",
            "cancel_at_period_end": True,
            "current_period_end": period_end,
        })
        assert r.status_code == 200

        data = client.get("/api/billing/subscription", headers=hdr).json()
        assert data["status"] == "past_due"
        assert data["cancel_at_period_end"] is True
        expected_date = datetime.fromtimestamp(period_end, tz=timezone.utc).date().isoformat()
        assert expected_date in data["current_period_end"]

    def test_past_due_loses_paid_limits(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        # Fill the crud window to the free cap (5)
        for i in range(5):
            assert client.post(
                "/api/projects", json={"name": f"P{i}"}, headers=hdr,
            ).status_code == 201

        _activate_pro(client, user_id)
        _webhook(client, "customer.subscription.updated", {
            "id": "sub_test_1", "status": "past_due",
        })
        r = client.post("/api/projects", json={"name": "X"}, headers=hdr)
        assert r.status_code == 429

    def test_payment_failed_sets_past_due(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        _activate_pro(client, user_id)

        r = _webhook(client, "invoice.payment_failed", {"subscription": "sub_test_1"})
        assert r.status_code == 200
        assert r.json()["handled"] == "invoice.payment_failed"

        data = client.get("/api/billing/subscription", headers=hdr).json()
        assert data["status"] == "past_due"

    def test_webhook_without_metadata_ignored(self, client):
        r = _webhook(client, "checkout.session.completed", {"id": "cs_x"})
        assert r.status_code == 200
        assert r.json()["handled"] is None

    def test_paid_plan_isolated_per_user(self, client):
        hdr_a = _auth(client, "a@f.dev", "alice")
        hdr_b = _auth(client, "b@f.dev", "bobxx")
        _activate_pro(client, _user_id(client, "a@f.dev"))

        # Alice (pro): 6 creates all pass the free-plan cap
        for i in range(6):
            assert client.post(
                "/api/projects", json={"name": f"A{i}"}, headers=hdr_a,
            ).status_code == 201
        # Bob (free): capped at 5
        for i in range(5):
            assert client.post(
                "/api/projects", json={"name": f"B{i}"}, headers=hdr_b,
            ).status_code == 201
        assert client.post(
            "/api/projects", json={"name": "B5"}, headers=hdr_b,
        ).status_code == 429


# ═══════════════════════════════════════════════════════════════════════════════
#  Cancel
# ═══════════════════════════════════════════════════════════════════════════════

class TestCancel:
    def test_no_paid_subscription_400(self, client):
        hdr = _auth(client)
        r = client.post("/api/billing/cancel", headers=hdr)
        assert r.status_code == 400

    def test_cancel_via_stripe_at_period_end(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        _activate_pro(client, user_id)

        with patch("server.billing.stripe_api.cancel_subscription_at_period_end") as mc:
            r = client.post("/api/billing/cancel", headers=hdr)

        assert r.status_code == 200
        mc.assert_called_once_with("sub_test_1")
        data = r.json()
        assert data["plan_id"] == "pro"  # stays pro until period end
        assert data["cancel_at_period_end"] is True

    def test_cancel_manual_subscription_downgrades(self, client):
        hdr = _auth(client)
        user_id = _user_id(client)
        _activate_pro(client, user_id)

        # Detach the Stripe subscription (simulates a manual/dev upgrade)
        db = client._session()
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        sub.stripe_subscription_id = None
        db.commit()
        db.close()

        r = client.post("/api/billing/cancel", headers=hdr)
        assert r.status_code == 200
        data = r.json()
        assert data["plan_id"] == "free"
        assert data["cancel_at_period_end"] is False

    def test_requires_auth(self, client):
        r = client.post("/api/billing/cancel")
        assert r.status_code == 401
