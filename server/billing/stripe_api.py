"""Thin wrapper over the official Stripe SDK.

All Stripe network calls live here so the rest of the billing code stays
SDK-agnostic and tests can monkeypatch a single module.
"""
from __future__ import annotations

import os

import stripe

from ..auth.models import User
from .plans import Plan

API_KEY_ENV = "STRIPE_SECRET_KEY"


class BillingNotConfigured(Exception):
    """Raised when a required Stripe setting is missing."""


def is_configured() -> bool:
    return bool(os.environ.get(API_KEY_ENV))


def _client() -> stripe.StripeClient:
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise BillingNotConfigured(f"Stripe is not configured (set {API_KEY_ENV})")
    return stripe.StripeClient(key)


def ensure_customer(user: User) -> str:
    """Create a Stripe customer for the user and return its ID."""
    client = _client()
    customer = client.v1.customers.create(
        email=user.email,
        name=user.username,
        metadata={"user_id": user.id},
    )
    return customer["id"]


def require_price(plan: Plan) -> str:
    """Return the plan's Stripe Price ID, raising if not configured."""
    price_id = plan.stripe_price_id
    if price_id is None:
        raise BillingNotConfigured(
            f"Stripe Price ID for plan '{plan.id}' is not configured "
            f"(set {plan.stripe_price_env})"
        )
    return price_id


def create_checkout_session(
    *,
    customer_id: str,
    plan: Plan,
    user_id: str,
    success_url: str,
    cancel_url: str,
) -> dict:
    """Create a Stripe Checkout session for a paid plan."""
    price_id = require_price(plan)
    client = _client()
    session = client.v1.checkout.sessions.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        metadata={"user_id": user_id, "plan_id": plan.id},
        subscription_data={"metadata": {"user_id": user_id, "plan_id": plan.id}},
    )
    return {"id": session["id"], "url": session["url"]}


def cancel_subscription_at_period_end(subscription_id: str) -> None:
    """Mark a Stripe subscription for cancellation at period end."""
    client = _client()
    client.v1.subscriptions.update(subscription_id, cancel_at_period_end=True)
