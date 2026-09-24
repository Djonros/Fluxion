"""Usage stats API routes.

    GET /api/usage              — summary (total requests, avg latency, error rate)
    GET /api/usage/breakdown    — per-endpoint breakdown
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import Integer, case, func

from ..auth.dependencies import get_current_user
from ..auth.models import User
from ..database import get_db
from .models import ApiUsageLog

router = APIRouter(prefix="/api/usage", tags=["usage"])


# ── response schemas ─────────────────────────────────────────────────────────

class UsageSummary(BaseModel):
    total_requests: int
    total_errors: int
    error_rate: float
    avg_latency_ms: float
    tokens_in: int
    tokens_out: int
    period_hours: int


class EndpointStat(BaseModel):
    endpoint: str
    method: str
    count: int
    errors: int
    avg_latency_ms: float


class UsageBreakdown(BaseModel):
    endpoints: list[EndpointStat]


# ── routes ───────────────────────────────────────────────────────────────────

@router.get("", response_model=UsageSummary)
def usage_summary(
    hours: int = Query(default=24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Aggregated usage stats for the authenticated user over the last N hours."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    logs = db.query(ApiUsageLog).filter(
        ApiUsageLog.user_id == user.id,
        ApiUsageLog.created_at >= since,
    )

    total = logs.count()
    errors = logs.filter(ApiUsageLog.status_code >= 400).count()
    avg_lat = db.query(func.avg(ApiUsageLog.latency_ms)).filter(
        ApiUsageLog.user_id == user.id,
        ApiUsageLog.created_at >= since,
    ).scalar() or 0.0
    tokens_in = db.query(func.coalesce(func.sum(ApiUsageLog.tokens_in), 0)).filter(
        ApiUsageLog.user_id == user.id,
        ApiUsageLog.created_at >= since,
    ).scalar() or 0
    tokens_out = db.query(func.coalesce(func.sum(ApiUsageLog.tokens_out), 0)).filter(
        ApiUsageLog.user_id == user.id,
        ApiUsageLog.created_at >= since,
    ).scalar() or 0

    return UsageSummary(
        total_requests=total,
        total_errors=errors,
        error_rate=round(errors / total, 4) if total else 0.0,
        avg_latency_ms=round(avg_lat, 1),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        period_hours=hours,
    )


@router.get("/breakdown", response_model=UsageBreakdown)
def usage_breakdown(
    hours: int = Query(default=24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db=Depends(get_db),
):
    """Per-endpoint usage breakdown for the authenticated user."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    rows = (
        db.query(
            ApiUsageLog.endpoint,
            ApiUsageLog.method,
            func.count().label("count"),
            func.sum(
                func.cast(
                    case((ApiUsageLog.status_code >= 400, 1), else_=0),
                    Integer,
                )
            ).label("errors"),
            func.avg(ApiUsageLog.latency_ms).label("avg_latency"),
        )
        .filter(
            ApiUsageLog.user_id == user.id,
            ApiUsageLog.created_at >= since,
        )
        .group_by(ApiUsageLog.endpoint, ApiUsageLog.method)
        .order_by(func.count().desc())
        .all()
    )

    return UsageBreakdown(
        endpoints=[
            EndpointStat(
                endpoint=r.endpoint,
                method=r.method,
                count=r.count,
                errors=r.errors or 0,
                avg_latency_ms=round(r.avg_latency or 0, 1),
            )
            for r in rows
        ]
    )
