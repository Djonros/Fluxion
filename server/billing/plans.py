"""Billing plan catalog — static definition of subscription tiers.

Limits are requests per minute per rate-limit tier (see usage/limiter.py).
The free plan mirrors the global DEFAULT_LIMITS; paid plans raise them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    """A subscription tier: price + per-tier rate limits (requests/minute)."""

    id: str
    name: str
    description: str
    price_monthly_usd: int
    limits: dict[str, int]
    stripe_price_env: str | None = None

    @property
    def stripe_price_id(self) -> str | None:
        """Resolve the Stripe Price ID from the environment (or None)."""
        if self.stripe_price_env is None:
            return None
        return os.environ.get(self.stripe_price_env) or None

    @property
    def is_free(self) -> bool:
        return self.id == "free"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "price_monthly_usd": self.price_monthly_usd,
            "limits": dict(self.limits),
        }


PLANS: dict[str, Plan] = {
    "free": Plan(
        id="free",
        name="Free",
        description="For local experiments and evaluation.",
        price_monthly_usd=0,
        limits={"auth": 10, "chat": 30, "crud": 100, "usage": 20, "billing": 30, "default": 60},
    ),
    "pro": Plan(
        id="pro",
        name="Pro",
        description="For individual developers using Fluxion daily.",
        price_monthly_usd=19,
        limits={"auth": 10, "chat": 120, "crud": 400, "usage": 60, "billing": 60, "default": 120},
        stripe_price_env="STRIPE_PRICE_PRO",
    ),
    "team": Plan(
        id="team",
        name="Team",
        description="For teams sharing projects and heavier agent workloads.",
        price_monthly_usd=49,
        limits={"auth": 10, "chat": 300, "crud": 1000, "usage": 120, "billing": 120, "default": 240},
        stripe_price_env="STRIPE_PRICE_TEAM",
    ),
    "enterprise": Plan(
        id="enterprise",
        name="Enterprise",
        description="For organizations: domain-based member onboarding, "
                    "inherited limits for all members, and org-level analytics.",
        price_monthly_usd=199,
        limits={"auth": 10, "chat": 600, "crud": 2000, "usage": 240, "billing": 240, "default": 480},
        stripe_price_env="STRIPE_PRICE_ENTERPRISE",
    ),
}


def get_plan(plan_id: str | None) -> Plan | None:
    """Return the plan by ID, or None."""
    if not plan_id:
        return None
    return PLANS.get(plan_id)
