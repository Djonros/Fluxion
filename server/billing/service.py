"""Billing service layer — subscriptions and rate-limit integration.

Bridges the Subscription table with the RateLimiter: the limiter gets a
resolver callback that maps user → plan limits, so paid quotas survive a
server restart (they are re-read from the database on cache miss).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..usage.limiter import get_limiter
from .models import Subscription
from .plans import PLANS

#: Subscription statuses that grant paid-plan limits.
ACTIVE_STATUSES = {"trialing", "active"}

_session_factory = None


def set_session_factory(factory) -> None:
    """Set the DB session factory used by resolve_user_limits (for tests)."""
    global _session_factory
    _session_factory = factory


def _session() -> Session:
    if _session_factory is not None:
        return _session_factory()
    from ..database import SessionLocal
    return SessionLocal()


def get_or_create_subscription(db: Session, user_id: str) -> Subscription:
    """Fetch the user's subscription row, creating a free one if missing."""
    sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
    if sub is None:
        sub = Subscription(user_id=user_id, plan_id="free", status="active")
        db.add(sub)
        db.commit()
        db.refresh(sub)
    return sub


def apply_plan_limits(user_id: str) -> None:
    """Invalidate the limiter's cached limits so the next request re-resolves."""
    get_limiter().clear_user_limits(user_id)


def resolve_user_limits(user_id: str) -> dict[str, int] | None:
    """RateLimiter callback: return paid-plan limits for the user, or None.

    None (free plan / no subscription / inactive status) means "use the
    global default limits".

    Enterprise inheritance: members of an organization whose owner holds an
    active enterprise subscription inherit its limits (per-tier max is used
    when the user also has a personal paid plan).
    """
    db = _session()
    try:
        sub = db.query(Subscription).filter(Subscription.user_id == user_id).first()
        org_limits = _enterprise_org_limits(db, user_id)
    finally:
        db.close()

    own_limits = None
    if sub is not None and sub.status in ACTIVE_STATUSES:
        plan = PLANS.get(sub.plan_id)
        if plan is not None and not plan.is_free:
            own_limits = dict(plan.limits)

    if org_limits is None:
        return own_limits
    if own_limits is None:
        return org_limits
    return {
        tier: max(own_limits.get(tier, 0), org_limits.get(tier, 0))
        for tier in set(own_limits) | set(org_limits)
    }


def _enterprise_org_limits(db: Session, user_id: str) -> dict[str, int] | None:
    """Return enterprise limits if the user's org owner has an active sub, else None."""
    plan = PLANS.get("enterprise")
    if plan is None:
        return None
    from ..organizations.models import Organization, OrgMember

    owner_ids = [
        row[0]
        for row in db.query(Organization.owner_id)
        .join(OrgMember, OrgMember.org_id == Organization.id)
        .filter(OrgMember.user_id == user_id)
        .distinct()
        .all()
    ]
    if not owner_ids:
        return None
    active = (
        db.query(Subscription)
        .filter(
            Subscription.user_id.in_(owner_ids),
            Subscription.plan_id == "enterprise",
            Subscription.status.in_(ACTIVE_STATUSES),
        )
        .first()
    )
    if active is None:
        return None
    return dict(plan.limits)
