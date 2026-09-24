"""Auth API routes: register, login, refresh, me."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..usage.limiter import rate_limit
from .dependencies import get_current_user
from .jwt_handler import create_access_token, create_refresh_token, decode_token
from .models import User
from .password import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ── request / response schemas ───────────────────────────────────────────────

class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=100)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    username: str
    is_active: bool
    is_admin: bool
    created_at: str | None = None


# ── routes ───────────────────────────────────────────────────────────────────

@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(req: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Register a new user account."""
    rate_limit(request)
    existing = db.query(User).filter(
        (User.email == req.email) | (User.username == req.username)
    ).first()
    if existing:
        field = "email" if existing.email == req.email else "username"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with this {field} already exists",
        )

    user = User(
        email=req.email,
        username=req.username,
        hashed_password=hash_password(req.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    _auto_join_organization(db, user)

    return _create_tokens(user)


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticate and return tokens."""
    rate_limit(request)
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    return _create_tokens(user)


@router.post("/refresh", response_model=TokenResponse)
def refresh(req: RefreshRequest, request: Request, db: Session = Depends(get_db)):
    """Exchange a refresh token for a new access + refresh pair."""
    rate_limit(request)
    payload = decode_token(req.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or disabled",
        )

    return _create_tokens(user)


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)):
    """Return the current authenticated user's profile."""
    return UserResponse(**user.to_dict())


# ── helpers ──────────────────────────────────────────────────────────────────

def _auto_join_organization(db: Session, user: User) -> None:
    """SSO-lite: auto-join an organization whose email_domain matches the user's email."""
    from ..organizations.models import Organization, OrgMember

    domain = user.email.rsplit("@", 1)[-1].lower()
    org = db.query(Organization).filter(Organization.email_domain == domain).first()
    if org is None:
        return
    db.add(OrgMember(org_id=org.id, user_id=user.id, role="member"))
    db.commit()


def _create_tokens(user: User) -> TokenResponse:
    """Generate access + refresh tokens for *user*."""
    token_data = {"sub": user.id, "email": user.email, "username": user.username}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )
