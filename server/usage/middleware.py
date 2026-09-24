"""Middleware that logs every /api/ request to the ApiUsageLog table."""
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from ..database import SessionLocal
from .models import ApiUsageLog


class UsageLoggingMiddleware(BaseHTTPMiddleware):
    """Records every /api/ request with latency, status code, and user."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if not path.startswith("/api/"):
            return await call_next(request)

        start = time.monotonic()
        response: Response | None = None
        error_msg: str | None = None

        try:
            response = await call_next(request)
        except Exception as exc:
            error_msg = str(exc)[:500]
            raise
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)
            status_code = response.status_code if response else 500
            user_id = getattr(request.state, "user_id", None)

            try:
                session_factory = getattr(
                    request.app.state, "session_factory", SessionLocal,
                )
                _log_request(
                    session_factory=session_factory,
                    user_id=user_id,
                    endpoint=path,
                    method=request.method,
                    status_code=status_code,
                    latency_ms=latency_ms,
                    error=error_msg,
                )
            except Exception:
                pass

        return response


def _log_request(
    session_factory,
    user_id: str | None,
    endpoint: str,
    method: str,
    status_code: int,
    latency_ms: int,
    error: str | None = None,
) -> None:
    """Insert an ApiUsageLog record."""
    db = session_factory()
    try:
        log = ApiUsageLog(
            user_id=user_id,
            endpoint=endpoint,
            method=method,
            status_code=status_code,
            latency_ms=latency_ms,
            error=error,
        )
        db.add(log)
        db.commit()
    finally:
        db.close()
