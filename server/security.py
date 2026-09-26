"""Deployment-mode security policy for the Fluxion server.

Two modes, selected with ``FLUXION_SERVER_MODE``:

* ``local`` (default) — single user on their own machine (VS Code extension,
  desktop).  The agent may write files and run tests in the user's projects.
* ``saas`` — multi-tenant hosted service.  pytest executes project code
  (conftest.py) with the server's privileges, so in this mode the agent is
  read-only, cannot run tests, and every path is confined to the caller's
  own workspace directory (``FLUXION_WORKSPACES_DIR/<user_id>``).
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import HTTPException, status

_LOCAL_ORIGIN_REGEX = r"^(https?://(localhost|127\.0\.0\.1|\[::1\])(:\d+)?|vscode-webview://.*)$"


def server_mode() -> str:
    mode = os.environ.get("FLUXION_SERVER_MODE", "local").strip().lower()
    return mode if mode in ("local", "saas") else "local"


def is_saas() -> bool:
    return server_mode() == "saas"


def is_production() -> bool:
    return is_saas() or os.environ.get("FLUXION_ENV", "").strip().lower() in ("prod", "production")


def workspaces_root() -> Path:
    return Path(os.environ.get("FLUXION_WORKSPACES_DIR", "data/workspaces")).resolve()


def user_workspace(user_id: object) -> Path:
    path = workspaces_root() / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_user_path(user_id: object, raw: str | None) -> Path:
    """Resolve a client-supplied path under the security policy.

    ``saas``: relative paths are resolved inside the user's workspace; any path
    escaping it is rejected with 403.  ``local``: the path is used as given.
    """
    if not is_saas():
        return Path(raw or ".").resolve()
    base = user_workspace(user_id)
    candidate = Path(raw or ".")
    candidate = (candidate if candidate.is_absolute() else base / candidate).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Path is outside your workspace",
        ) from None
    return candidate


def cors_options() -> dict:
    """CORS config: explicit ``FLUXION_CORS_ORIGINS`` (comma-separated) or,
    by default, only localhost and VS Code webviews."""
    raw = os.environ.get("FLUXION_CORS_ORIGINS", "").strip()
    opts: dict = {
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if raw:
        opts["allow_origins"] = [o.strip() for o in raw.split(",") if o.strip()]
    else:
        opts["allow_origin_regex"] = _LOCAL_ORIGIN_REGEX
    return opts
