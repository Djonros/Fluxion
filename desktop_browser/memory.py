"""Persistent chat memory: the history of chats on disk (``data/chats.json``).

A chat keeps what the model sees (``messages``: role and text) and what the
window shows (``view``: the same answers with their sources), the chat chips
(``mode``), the model and the project folder it was held in.  The file of the
older versions (only ``messages``) is read as is.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from datetime import datetime
from pathlib import Path

_ROLES = ("user", "assistant")
_VIEW_ROLES = ("user", "assistant", "banner")
_MAX_SESSIONS = 500
TITLE_LENGTH = 60
DEFAULT_TITLE = "Чат"


def _base_dir() -> Path:
    from core.app_paths import data_dir

    return data_dir()


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


def _sanitize_sources(raw) -> list[dict]:
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("file"), str) and isinstance(item.get("label"), str):
            source = {"file": item["file"], "label": item["label"]}
            if "n" in item:
                source["n"] = item["n"]
            out.append(source)
    return out


def _sanitize_view(raw) -> list[dict]:
    """Messages as the window shows them; the spinner of an unfinished answer is not kept."""
    out: list[dict] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not (isinstance(item, dict) and item.get("role") in _VIEW_ROLES and isinstance(item.get("text"), str)):
            continue
        entry = {"role": item["role"], "text": item["text"], "meta": str(item.get("meta") or "")}
        sources = _sanitize_sources(item.get("sources"))
        if sources:
            entry["sources"] = sources
            if isinstance(item.get("sources_title"), str):
                entry["sources_title"] = item["sources_title"]
        out.append(entry)
    return out


def _sanitize_mode(raw) -> dict[str, bool]:
    raw = raw if isinstance(raw, dict) else {}
    return {"agent_files": bool(raw.get("agent_files")), "plan": bool(raw.get("plan"))}


def session_title(messages: list[dict[str, str]]) -> str:
    """The first question, in one line: the name of the chat in the list."""
    for msg in messages:
        if msg["role"] == "user" and msg["content"].strip():
            title = " ".join(msg["content"].split())
            return title[:TITLE_LENGTH]
    return DEFAULT_TITLE


def make_session(messages: list[dict[str, str]]) -> dict:
    now = time.time()
    return {
        "id": uuid.uuid4().hex,
        "title": session_title(messages),
        "created": now,
        "updated": now,
        "messages": [dict(m) for m in messages],
    }


def _normalize(item: dict) -> dict | None:
    messages = _sanitize_messages(item.get("messages"))
    if not messages:
        return None
    updated = float(item.get("updated") or 0)
    session = {
        "id": str(item.get("id") or uuid.uuid4().hex),
        "title": " ".join(str(item.get("title") or session_title(messages)).split())[:TITLE_LENGTH]
        or session_title(messages),
        "renamed": bool(item.get("renamed")),
        "created": float(item.get("created") or updated),
        "updated": updated,
        "messages": messages,
        "mode": _sanitize_mode(item.get("mode")),
        "model": str(item.get("model") or ""),
        "project": str(item.get("project") or ""),
    }
    view = _sanitize_view(item.get("view"))
    if view:
        session["view"] = view
    return session


def _write(sessions: list[dict]) -> None:
    """Write through a temporary file: a crash mid-write leaves the old history whole."""
    try:
        path = chats_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(sessions, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)
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
            if isinstance(item, dict):
                session = _normalize(item)
                if session is not None:
                    sessions.append(session)

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
    session = _normalize(make_session(messages))
    _write([session])
    return [session]


def save_session(session: dict) -> list[dict]:
    """Insert or update *session*; returns the updated list (oldest first).

    A chat renamed by hand keeps its name: the saved one wins over the title
    the window derives from the first question."""
    old = load_sessions()
    previous = next((s for s in old if s["id"] == session["id"]), None)
    item = dict(session)
    item.setdefault("updated", time.time())
    if previous is not None:
        item.setdefault("created", previous["created"])
        if previous["renamed"] and not item.get("renamed"):
            item["title"], item["renamed"] = previous["title"], True
    normalized = _normalize(item)
    sessions = [s for s in old if s["id"] != session["id"]]
    if normalized is not None:
        sessions.append(normalized)
    sessions.sort(key=lambda s: s["updated"])
    sessions = sessions[-_MAX_SESSIONS:]
    _write(sessions)
    return sessions


def rename_session(session_id: str, title: str) -> list[dict]:
    """Give a chat its own name; an empty name returns to the first question."""
    sessions = load_sessions()
    title = " ".join(title.split())[:TITLE_LENGTH]
    for s in sessions:
        if s["id"] == session_id:
            s["title"] = title or session_title(s["messages"])
            s["renamed"] = bool(title)
    _write(sessions)
    return sessions


def delete_sessions(session_ids) -> list[dict]:
    ids = set(session_ids)
    sessions = [s for s in load_sessions() if s["id"] not in ids]
    _write(sessions)
    return sessions


def delete_session(session_id: str) -> list[dict]:
    return delete_sessions([session_id])


def clear_sessions() -> list[dict]:
    """«Очистить всю историю»: the old single-chat file goes too, or it would come back."""
    _write([])
    try:
        history_path().unlink()
    except OSError:
        pass
    return []


def search_sessions(sessions: list[dict], query: str) -> list[dict]:
    """Chats whose name or messages hold every word of *query* (case does not matter)."""
    words = query.casefold().split()
    if not words:
        return list(sessions)
    found = []
    for s in sessions:
        text = " ".join([s["title"]] + [m["content"] for m in s["messages"]]).casefold()
        if all(w in text for w in words):
            found.append(s)
    return found


def day_group(timestamp: float, now: float | None = None) -> str:
    """"today", "yesterday" or "earlier": the heading a chat is listed under."""
    day = datetime.fromtimestamp(timestamp).date()
    today = datetime.fromtimestamp(time.time() if now is None else now).date()
    delta = (today - day).days
    if delta <= 0:
        return "today"
    if delta == 1:
        return "yesterday"
    return "earlier"


def session_to_markdown(session: dict) -> str:
    """Render a chat session as Markdown."""
    lines = [f"# {session.get('title') or DEFAULT_TITLE}", ""]
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
