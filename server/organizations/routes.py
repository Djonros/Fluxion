"""Organization API routes.

    CRUD /api/organizations                — create, list, get, members
    GET  /api/organizations/{id}/usage     — org-level usage analytics (owner)
    DEL  /api/organizations/{id}/members/… — remove member (owner)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Integer, case, func
from sqlalchemy.orm import Session

from ..auth.dependencies import get_current_user
from ..auth.models import User
from ..database import get_db
from ..usage.models import ApiUsageLog
from .models import Organization, OrgMember

router = APIRouter(prefix="/api/organizations", tags=["organizations"])


# ── request / response schemas ───────────────────────────────────────────────

class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email_domain: str = Field(min_length=3, max_length=255)


class OrganizationResponse(BaseModel):
    id: str
    name: str
    email_domain: str
    owner_id: str
    member_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class MemberResponse(BaseModel):
    user_id: str
    email: str
    username: str
    role: str
    joined_at: str | None = None


class OrgMemberUsage(BaseModel):
    user_id: str
    email: str
    username: str
    requests: int
    errors: int
    tokens_in: int
    tokens_out: int


class OrgUsageResponse(BaseModel):
    org_id: str
    period_hours: int
    member_count: int
    total_requests: int
    total_errors: int
    error_rate: float
    avg_latency_ms: float
    tokens_in: int
    tokens_out: int
    members: list[OrgMemberUsage]


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("", response_model=OrganizationResponse, status_code=status.HTTP_201_CREATED)
def create_organization(
    req: OrganizationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create an organization; the creator becomes its owner."""
    domain = _normalize_domain(req.email_domain)
    if db.query(Organization).filter(Organization.email_domain == domain).first():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Domain '{domain}' is already claimed by another organization",
        )
    org = Organization(name=req.name, email_domain=domain, owner_id=user.id)
    db.add(org)
    db.flush()
    db.add(OrgMember(org_id=org.id, user_id=user.id, role="owner"))
    db.commit()
    db.refresh(org)
    return _org_response(db, org)


@router.get("", response_model=list[OrganizationResponse])
def list_organizations(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List all organizations the authenticated user belongs to."""
    orgs = (
        db.query(Organization)
        .join(OrgMember, OrgMember.org_id == Organization.id)
        .filter(OrgMember.user_id == user.id)
        .order_by(Organization.created_at.desc())
        .all()
    )
    return [_org_response(db, org) for org in orgs]


@router.get("/{org_id}", response_model=OrganizationResponse)
def get_organization(
    org_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get a single organization (must be a member)."""
    org = _get_member_org(db, org_id, user.id)
    return _org_response(db, org)


@router.get("/{org_id}/members", response_model=list[MemberResponse])
def list_members(
    org_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List organization members with user info (must be a member)."""
    _get_member_org(db, org_id, user.id)
    members = (
        db.query(OrgMember, User)
        .join(User, User.id == OrgMember.user_id)
        .filter(OrgMember.org_id == org_id)
        .order_by(OrgMember.joined_at.asc())
        .all()
    )
    return [
        MemberResponse(
            user_id=member.user_id,
            email=user_.email,
            username=user_.username,
            role=member.role,
            joined_at=member.joined_at.isoformat() if member.joined_at else None,
        )
        for member, user_ in members
    ]


@router.get("/{org_id}/usage", response_model=OrgUsageResponse)
def organization_usage(
    org_id: str,
    hours: int = Query(default=24, ge=1, le=720),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Aggregated usage stats across all organization members (owner only)."""
    org = _get_member_org(db, org_id, user.id)
    if org.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization owner can view org usage",
        )

    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    member_rows = (
        db.query(OrgMember.user_id, User.email, User.username)
        .join(User, User.id == OrgMember.user_id)
        .filter(OrgMember.org_id == org_id)
        .order_by(OrgMember.joined_at.asc())
        .all()
    )
    user_ids = [row[0] for row in member_rows]
    member_count = len(user_ids)

    totals = _org_totals(db, user_ids, since)
    per_member = (
        db.query(
            ApiUsageLog.user_id,
            func.count().label("requests"),
            func.sum(
                func.cast(case((ApiUsageLog.status_code >= 400, 1), else_=0), Integer)
            ).label("errors"),
            func.coalesce(func.sum(ApiUsageLog.tokens_in), 0).label("tokens_in"),
            func.coalesce(func.sum(ApiUsageLog.tokens_out), 0).label("tokens_out"),
        )
        .filter(
            ApiUsageLog.user_id.in_(user_ids),
            ApiUsageLog.created_at >= since,
        )
        .group_by(ApiUsageLog.user_id)
        .all()
    )
    per_member_map = {r.user_id: r for r in per_member}

    members = []
    for user_id, email, username in member_rows:
        r = per_member_map.get(user_id)
        members.append(OrgMemberUsage(
            user_id=user_id,
            email=email,
            username=username,
            requests=r.requests if r else 0,
            errors=r.errors or 0 if r else 0,
            tokens_in=r.tokens_in if r else 0,
            tokens_out=r.tokens_out if r else 0,
        ))

    total = totals.total_requests
    return OrgUsageResponse(
        org_id=org_id,
        period_hours=hours,
        member_count=member_count,
        total_requests=total,
        total_errors=totals.total_errors,
        error_rate=round(totals.total_errors / total, 4) if total else 0.0,
        avg_latency_ms=round(totals.avg_latency_ms, 1),
        tokens_in=totals.tokens_in,
        tokens_out=totals.tokens_out,
        members=members,
    )


@router.delete("/{org_id}/members/{member_user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    org_id: str,
    member_user_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a member from an organization (owner only, cannot remove the owner)."""
    org = _get_member_org(db, org_id, user.id)
    if org.owner_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the organization owner can remove members",
        )
    if member_user_id == org.owner_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Organization owner cannot be removed",
        )
    member = db.query(OrgMember).filter(
        OrgMember.org_id == org_id, OrgMember.user_id == member_user_id,
    ).first()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found",
        )
    db.delete(member)
    db.commit()


# ── helpers ──────────────────────────────────────────────────────────────────

class _OrgTotals(NamedTuple):
    total_requests: int
    total_errors: int
    avg_latency_ms: float
    tokens_in: int
    tokens_out: int


def _org_totals(db: Session, user_ids: list[str], since: datetime) -> _OrgTotals:
    """Aggregate ApiUsageLog totals across the given users since *since*."""
    if not user_ids:
        return _OrgTotals(0, 0, 0.0, 0, 0)
    cond = (
        ApiUsageLog.user_id.in_(user_ids),
        ApiUsageLog.created_at >= since,
    )
    row = db.query(
        func.count().label("total"),
        func.sum(
            func.cast(case((ApiUsageLog.status_code >= 400, 1), else_=0), Integer)
        ).label("errors"),
        func.avg(ApiUsageLog.latency_ms).label("avg_lat"),
        func.coalesce(func.sum(ApiUsageLog.tokens_in), 0).label("tokens_in"),
        func.coalesce(func.sum(ApiUsageLog.tokens_out), 0).label("tokens_out"),
    ).filter(*cond).one()
    return _OrgTotals(
        total_requests=row.total or 0,
        total_errors=row.errors or 0,
        avg_latency_ms=row.avg_lat or 0.0,
        tokens_in=row.tokens_in or 0,
        tokens_out=row.tokens_out or 0,
    )


def _normalize_domain(raw: str) -> str:
    """Lowercase and strip a leading '@' from an email domain."""
    domain = raw.strip().lower().lstrip("@")
    if "." not in domain or domain.startswith(".") or domain.endswith("."):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid email domain: '{raw}'",
        )
    return domain


def _get_member_org(db: Session, org_id: str, user_id: str) -> Organization:
    """Fetch an org and verify membership. Raises 404 if not found or not a member."""
    org = (
        db.query(Organization)
        .join(OrgMember, OrgMember.org_id == Organization.id)
        .filter(Organization.id == org_id, OrgMember.user_id == user_id)
        .first()
    )
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization not found",
        )
    return org


def _org_response(db: Session, org: Organization) -> OrganizationResponse:
    member_count = (
        db.query(OrgMember).filter(OrgMember.org_id == org.id).count()
    )
    data = org.to_dict()
    data["member_count"] = member_count
    return OrganizationResponse(**data)
