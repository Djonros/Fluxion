"""Stripe webhook signature verification."""
from __future__ import annotations

import os

import stripe
from fastapi import HTTPException, status

WEBHOOK_SECRET_ENV = "STRIPE_WEBHOOK_SECRET"


def verify_signature(payload: bytes, sig_header: str) -> dict:
    """Verify the Stripe-Signature header and return the parsed event.

    Raises 503 if no webhook secret is configured, 400 on bad payload or
    signature.
    """
    secret = os.environ.get(WEBHOOK_SECRET_ENV, "")
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Stripe webhook secret is not configured (set {WEBHOOK_SECRET_ENV})",
        )
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        )
    except stripe.error.SignatureVerificationError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid signature",
        )
    return event.to_dict()
