"""Password hashing using bcrypt directly (passlib is incompatible with bcrypt>=4.1)."""
from __future__ import annotations

import bcrypt

_BCRYPT_MAX_BYTES = 72


def _truncate(password: str) -> bytes:
    """Encode and truncate password to bcrypt's 72-byte limit."""
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash a plaintext password and return a bcrypt hash string."""
    return bcrypt.hashpw(_truncate(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(plaintext: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(_truncate(plaintext), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False
