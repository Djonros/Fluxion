"""In-memory sliding-window rate limiter.

Tiers (requests per minute):
    auth     →  10  (login, register, refresh)
    chat     →  30  (chat, agent, session chat)
    crud     → 100  (projects, sessions, messages)
    usage    →  20  (stats endpoints)
    billing  →  30  (plans, subscription, checkout)
    default  →  60

Per-user overrides: a limit resolver callback (user_id → plan limits)
can raise limits for paid plans; results are cached until invalidated
via clear_user_limits (billing calls it when the plan changes).

Designed for single-process deployment. For multi-process, swap the
internal dicts with Redis.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status


@dataclass
class _Window:
    """Sliding window state for one user+tier."""
    timestamps: list[float] = field(default_factory=list)


DEFAULT_LIMITS: dict[str, int] = {
    "auth": 10,
    "chat": 30,
    "crud": 100,
    "usage": 20,
    "billing": 30,
    "default": 60,
}

_WINDOW_SECONDS = 60


def _classify(path: str) -> str:
    """Classify an endpoint path into a rate-limit tier."""
    if "/api/auth/" in path:
        return "auth"
    if "/api/chat" in path or "/api/agent/" in path or "/sessions/" in path and "/chat" in path:
        return "chat"
    if "/api/usage" in path:
        return "usage"
    if "/api/billing" in path:
        return "billing"
    if "/api/projects" in path or "/api/sessions" in path or "/api/organizations" in path:
        return "crud"
    return "default"


class RateLimiter:
    """Thread-safe-enough rate limiter for single-process FastAPI.

    Uses a per-user dict of timestamp lists. Expired entries are pruned
    on every check.
    """

    def __init__(
        self,
        limits: dict[str, int] | None = None,
        limit_resolver: Callable[[str], dict[str, int] | None] | None = None,
    ) -> None:
        self.limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._limit_resolver = limit_resolver
        self._buckets: dict[str, dict[str, _Window]] = {}
        self._user_limits: dict[str, dict[str, int]] = {}

    def set_limit_resolver(self, resolver: Callable[[str], dict[str, int] | None]) -> None:
        """Set the callback mapping user_id → per-user plan limits (or None)."""
        self._limit_resolver = resolver

    def set_user_limits(self, user_id: str, limits: dict[str, int]) -> None:
        """Directly set per-user limits (overrides the resolver for this user)."""
        self._user_limits[user_id] = dict(limits)

    def clear_user_limits(self, user_id: str) -> None:
        """Drop cached per-user limits so the next check re-resolves."""
        self._user_limits.pop(user_id, None)

    def _effective_limit(self, user_id: str, tier: str) -> int:
        """Resolve the limit: per-user plan override → tier default."""
        user_limits = self._user_limits.get(user_id)
        if user_limits is None and self._limit_resolver is not None:
            resolved = self._limit_resolver(user_id)
            user_limits = resolved if resolved is not None else {}
            self._user_limits[user_id] = user_limits
        if user_limits and tier in user_limits:
            return user_limits[tier]
        return self.limits.get(tier, self.limits["default"])

    def check(self, user_id: str, tier: str) -> None:
        """Raise 429 if the user exceeded the tier limit. Otherwise record hit."""
        limit = self._effective_limit(user_id, tier)
        now = time.monotonic()

        user_buckets = self._buckets.setdefault(user_id, {})
        window = user_buckets.setdefault(tier, _Window())

        window.timestamps = [t for t in window.timestamps if now - t < _WINDOW_SECONDS]

        if len(window.timestamps) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({limit} requests per minute for tier '{tier}')",
                headers={"Retry-After": str(_WINDOW_SECONDS)},
            )

        window.timestamps.append(now)

    def remaining(self, user_id: str, tier: str) -> int:
        """Return remaining requests in the current window."""
        limit = self._effective_limit(user_id, tier)
        now = time.monotonic()
        user_buckets = self._buckets.get(user_id, {})
        window = user_buckets.get(tier)
        if window is None:
            return limit
        window.timestamps = [t for t in window.timestamps if now - t < _WINDOW_SECONDS]
        return max(0, limit - len(window.timestamps))

    def reset(self) -> None:
        """Clear all buckets and per-user limits (for testing)."""
        self._buckets.clear()
        self._user_limits.clear()


_global_limiter: RateLimiter | None = None


def get_limiter() -> RateLimiter:
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter()
    return _global_limiter


def set_limiter(limiter: RateLimiter) -> None:
    global _global_limiter
    _global_limiter = limiter


def rate_limit(request: Request, user_id: str | None = None) -> None:
    """FastAPI-callable rate limiter.

    If *user_id* is None, the client IP is used (for unauthenticated endpoints
    like login/register).
    """
    limiter = get_limiter()
    path = request.url.path
    tier = _classify(path)
    key = user_id or f"ip:{request.client.host if request.client else 'unknown'}"
    limiter.check(key, tier)
