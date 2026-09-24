"""Persistent chat memory: multiple saved chat sessions on disk."""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

_ROLES = ("user", "assistant")
_MAX_SESSIONS = 50


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    return base / "data"


def chats_path() -> Path:
    return _base_dir() / "chats.json"


def history_path() -> Path:
    """Legacy single-chat file (migrated automatically)."""
    return _base_dir() / "chat_history.json"


def _sanitize_messages(raw) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if (
            isinstance(item, dict)
            and item.get("role") in _ROLES
            and isinstance(item.get("content"), str)
        ):
            out.append({"role": item["role"], "content": item["content"]})
    return out


def session_title(messages: list[dict[str, str]]) -> str:
    for msg in messages:
        if msg["role"] == "user" and msg["content"].strip():
            title = " ".join(msg["content"].split())
            return title[:60]
    return "Р§Р°С‚"


def make_session(messages: list[dict[str, str]]) -> dict:
    return {
        "id": uuid.uuid4().hex,
        "title": session_title(messages),
        "updated": time.time(),
        "messages": [dict(m) for m in messages],
    }


def _write(sessions: list[dict]) -> None:
    try:
        path = chats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(sessions, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        pass


def load_sessions() -> list[dict]:
    """All saved chats, oldest first; never raises."""
    data: object = None
    try:
        data = json.loads(chats_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None

    sessions: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict):
                continue
            messages = _sanitize_messages(item.get("messages"))
            if not messages:
                continue
            sessions.append(
                {
                    "id": str(item.get("id") or uuid.uuid4().hex),
                    "title": str(item.get("title") or session_title(messages))[:60],
                    "updated": float(item.get("updated") or 0),
                    "messages": messages,
                }
            )

    if not sessions:
        migrated = _migrate_legacy()
        if migrated:
            return migrated

    sessions.sort(key=lambda s: s["updated"])
    return sessions[-_MAX_SESSIONS:]


def _migrate_legacy() -> list[dict] | None:
    """Import the old single chat_history.json once, if present."""
    try:
        raw = json.loads(history_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    messages = _sanitize_messages(raw)
    if not messages:
        return None
    session = make_session(messages)
    session["title"] = str(session["title"])
    _write([session])
    return [session]


def save_session(session: dict) -> list[dict]:
    """Insert or update *session*; returns the updated list (oldest first)."""
    sessions = [s for s in load_sessions() if s["id"] != session["id"]]
    sessions.append(
        {
            "id": session["id"],
            "title": session.get("title") or "Р§Р°С‚",
            "updated": float(session.get("updated") or time.time()),
            "messages": _sanitize_messages(session.get("messages")),
        }
    )
    sessions.sort(key=lambda s: s["updated"])
    sessions = sessions[-_MAX_SESSIONS:]
    _write(sessions)
    return sessions


def delete_session(session_id: str) -> list[dict]:
    sessions = [s for s in load_sessions() if s["id"] != session_id]
    _write(sessions)
    return sessions


def session_to_markdown(session: dict) -> str:
    """Render a chat session as Markdown."""
    lines = [f"# {session.get('title') or 'Чат'}", ""]
    for msg in session.get("messages", []):
        who = "Вы" if msg.get("role") == "user" else "Fluxion"
        lines.append(f"**{who}:**")
        lines.append("")
        lines.append(msg.get("content", "").strip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def safe_filename(title: str) -> str:
    keep = [c if (c.isalnum() or c in " _-.") else "" for c in title]
    name = "".join(keep).strip().replace(" ", "_")
    return (name[:50] or "chat") + ".md"
