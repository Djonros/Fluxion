"""Billing API routes — plans, subscription, checkout, Stripe webhook."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..auth.dependencies import get_current_user
from ..auth.models import User
from ..database import get_db
from . import stripe_api, webhook
from .models import Subscription
from .plans import get_plan
from .service import apply_plan_limits, get_or_create_subscription

router = APIRouter(prefix="/api/billing", tags=["billing"])
logger = logging.getLogger("fluxion.server.billing")


# ── request / response schemas ───────────────────────────────────────────────

class CheckoutRequest(BaseModel):
    plan_id: str
    success_url: str = Field(min_length=1)
    cancel_url: str = Field(min_length=1)


class SubscriptionResponse(BaseModel):
    id: str
    user_id: str
    plan_id: str
    plan_name: str
    status: str
    stripe_customer_id: str | None = None
    stripe_subscription_id: str | None = None
    cancel_at_period_end: bool = False
    current_period_end: str | None = None
    limits: dict
    created_at: str | None = None
    updated_at: str | None = None


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("/plans")
def list_plans():
    """List available billing plans (public)."""
    from .plans import PLANS
    return {"plans": [p.to_dict() for p in PLANS.values()]}


@router.get("/subscription", response_model=SubscriptionResponse)
def get_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get the current user's subscription (auto-created as free)."""
    sub = get_or_create_subscription(db, user.id)
    return _subscription_response(sub)


@router.post("/checkout")
def create_checkout(
    req: CheckoutRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Start a Stripe Checkout session for a paid plan."""
    plan = get_plan(req.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found",
        )
    if plan.is_free:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The free plan does not require checkout",
        )

    sub = get_or_create_subscription(db, user.id)
    try:
        stripe_api.require_price(plan)  # fail fast, before any Stripe calls
        if not sub.stripe_customer_id:
            sub.stripe_customer_id = stripe_api.ensure_customer(user)
            db.commit()
            db.refresh(sub)
        session = stripe_api.create_checkout_session(
            customer_id=sub.stripe_customer_id,
            plan=plan,
            user_id=user.id,
            success_url=req.success_url,
            cancel_url=req.cancel_url,
        )
    except stripe_api.BillingNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    return {
        "checkout_url": session["url"],
        "session_id": session["id"],
        "plan_id": plan.id,
    }


@router.post("/cancel", response_model=SubscriptionResponse)
def cancel_subscription(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Cancel the paid subscription (at period end via Stripe)."""
    sub = get_or_create_subscription(db, user.id)
    if sub.plan_id == "free" and not sub.stripe_subscription_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No paid subscription to cancel",
        )

    if sub.stripe_subscription_id:
        stripe_api.cancel_subscription_at_period_end(sub.stripe_subscription_id)
        sub.cancel_at_period_end = True
        db.commit()
        db.refresh(sub)
    else:
        # Manual/dev subscription without Stripe: downgrade immediately.
        sub.plan_id = "free"
        sub.status = "active"
        sub.cancel_at_period_end = False
        db.commit()
        db.refresh(sub)
        apply_plan_limits(user.id)

    return _subscription_response(sub)


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """Receive Stripe webhook events (signature-verified, unauthenticated)."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature", "")
    event = webhook.verify_signature(payload, sig_header)

    handled = _handle_event(event, db)
    return {"received": True, "handled": handled}


# ── event handling ───────────────────────────────────────────────────────────

def _handle_event(event: dict, db: Session) -> str | None:
    """Apply a verified Stripe event to the subscriptions table.

    Returns the handled event type, or None if ignored.
    """
    etype = event.get("type", "")
    obj = ((event.get("data") or {}).get("object") or {})

    if etype == "checkout.session.completed":
        return _on_checkout_completed(obj, db)

    if etype in ("customer.subscription.updated", "customer.subscription.deleted"):
        return _on_subscription_changed(etype, obj, db)

    if etype == "invoice.payment_failed":
        return _on_payment_failed(obj, db)

    return None


def _on_checkout_completed(obj: dict, db: Session) -> str | None:
    meta = obj.get("metadata") or {}
    user_id = meta.get("user_id")
    plan = get_plan(meta.get("plan_id"))
    if not user_id or plan is None:
        logger.warning("checkout.session.completed without usable metadata: %s", meta)
        return None

    sub = get_or_create_subscription(db, user_id)
    sub.plan_id = plan.id
    sub.status = "active"
    sub.stripe_customer_id = obj.get("customer") or sub.stripe_customer_id
    sub.stripe_subscription_id = obj.get("subscription") or sub.stripe_subscription_id
    sub.cancel_at_period_end = False
    db.commit()
    apply_plan_limits(user_id)
    return "checkout.session.completed"


def _on_subscription_changed(etype: str, obj: dict, db: Session) -> str | None:
    sub = _find_by_stripe_id(db, obj.get("id"))
    if sub is None:
        return None

    if etype == "customer.subscription.deleted":
        sub.status = "canceled"
        sub.plan_id = "free"
        sub.cancel_at_period_end = False
    else:
        sub.status = obj.get("status", sub.status)
        sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end", False))
        period_end = obj.get("current_period_end")
        if period_end:
            sub.current_period_end = datetime.fromtimestamp(period_end, tz=timezone.utc)

    db.commit()
    apply_plan_limits(sub.user_id)
    return etype


def _on_payment_failed(obj: dict, db: Session) -> str | None:
    sub = _find_by_stripe_id(db, obj.get("subscription"))
    if sub is None:
        return None
    sub.status = "past_due"
    db.commit()
    apply_plan_limits(sub.user_id)
    return "invoice.payment_failed"


def _find_by_stripe_id(db: Session, stripe_subscription_id: str | None) -> Subscription | None:
    if not stripe_subscription_id:
        return None
    return db.query(Subscription).filter(
        Subscription.stripe_subscription_id == stripe_subscription_id,
    ).first()


# ── helpers ──────────────────────────────────────────────────────────────────

def _subscription_response(sub: Subscription) -> SubscriptionResponse:
    plan = get_plan(sub.plan_id)
    return SubscriptionResponse(
        id=sub.id,
        user_id=sub.user_id,
        plan_id=sub.plan_id,
        plan_name=plan.name if plan else sub.plan_id,
        status=sub.status,
        stripe_customer_id=sub.stripe_customer_id,
        stripe_subscription_id=sub.stripe_subscription_id,
        cancel_at_period_end=sub.cancel_at_period_end,
        current_period_end=sub.current_period_end.isoformat() if sub.current_period_end else None,
        limits=dict(plan.limits) if plan else {},
        created_at=sub.created_at.isoformat() if sub.created_at else None,
        updated_at=sub.updated_at.isoformat() if sub.updated_at else None,
    )
