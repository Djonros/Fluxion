"""Authentication module: User model, password hashing, JWT, routes."""
from .models import User
from .password import hash_password, verify_password
from .jwt_handler import create_access_token, create_refresh_token, decode_token
from .dependencies import get_current_user, get_current_user_optional, require_auth
from .routes import router

__all__ = [
    "User",
    "hash_password",
    "verify_password",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "get_current_user",
    "get_current_user_optional",
    "require_auth",
    "router",
]
