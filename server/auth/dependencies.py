"""FastAPI auth dependencies — extract current user from JWT."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from ..database import get_db
from .jwt_handler import decode_token
from .models import User

_security = HTTPBearer(auto_error=False)


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_security),
    db: Session = Depends(get_db),
) -> User | None:
    """Return User if a valid token is present, None otherwise."""
    if credentials is None:
        return None
    if credentials.scheme.lower() != "bearer":
        return None

    payload = decode_token(credentials.credentials)
    if payload is None:
        return None
    if payload.get("type") != "access":
        return None

    user_id = payload.get("sub")
    if not user_id:
        return None

    return db.query(User).filter(User.id == user_id).first()


def get_current_user(
    request: Request,
    user: User | None = Depends(get_current_user_optional),
) -> User:
    """Require a valid authenticated user. Raises 401 if not authenticated.

    Also applies rate limiting per user and sets request.state.user_id
    for the usage logging middleware.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account disabled",
        )

    request.state.user_id = user.id

    from ..usage.limiter import rate_limit
    rate_limit(request, user.id)

    return user


def require_auth(user: User = Depends(get_current_user)) -> User:
    """Alias for get_current_user — explicit intent."""
    return user
