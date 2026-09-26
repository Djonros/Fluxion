"""JWT token creation and verification."""
from __future__ import annotations

import logging
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt

logger = logging.getLogger(__name__)

# The value that used to be hard-coded (and is public in the repository).
_KNOWN_INSECURE = {"fluxion-dev-secret-change-in-production", "changeme", "secret"}
_MIN_SECRET_LEN = 32


def _load_secret() -> str:
    """Signing secret.  A missing or known-public secret aborts startup in
    production; in development an ephemeral random secret is used (tokens are
    invalidated on restart and are not shared between worker processes)."""
    from ..security import is_production

    secret = os.environ.get("FLUXION_JWT_SECRET", "").strip()
    weak = not secret or secret in _KNOWN_INSECURE or len(secret) < _MIN_SECRET_LEN
    if weak and is_production():
        raise RuntimeError(
            "FLUXION_JWT_SECRET must be set to a random value of at least "
            f"{_MIN_SECRET_LEN} characters in production "
            '(python -c "import secrets; print(secrets.token_urlsafe(48))")'
        )
    if not secret or secret in _KNOWN_INSECURE:
        logger.warning(
            "FLUXION_JWT_SECRET is not set: using an ephemeral random secret "
            "(development only; sessions end on restart)."
        )
        return secrets.token_urlsafe(48)
    return secret


_SECRET_KEY = _load_secret()
_ALGORITHM = "HS256"
_ACCESS_TOKEN_EXPIRE_MINUTES = 30
_REFRESH_TOKEN_EXPIRE_DAYS = 7


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    """Create a short-lived access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=_ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    to_encode["type"] = "access"
    to_encode["jti"] = str(uuid.uuid4())
    return jwt.encode(to_encode, _SECRET_KEY, algorithm=_ALGORITHM)


def create_refresh_token(data: dict[str, Any]) -> str:
    """Create a long-lived refresh token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode["exp"] = expire
    to_encode["type"] = "refresh"
    to_encode["jti"] = str(uuid.uuid4())
    return jwt.encode(to_encode, _SECRET_KEY, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict[str, Any] | None:
    """Decode and verify a JWT. Returns payload dict or None."""
    try:
        payload = jwt.decode(token, _SECRET_KEY, algorithms=[_ALGORITHM])
        return payload
    except JWTError:
        return None
