"""Persistent chat memory: the history of chats on disk (``data/chats.db``).

A chat keeps what the model sees (``messages``: role and text) and what the
window shows (``view``: the same answers with their sources), the chat chips
(``mode``), the model and the project folder it was held in.  The file of the
older versions (only ``messages``) is read as is.

The chats are rows of a SQLite database, one a chat: saving a chat writes
that chat, not the whole history (it used to read and rewrite every chat on
every answer). ``chats.json`` of the versions up to 0.29 goes into the
database on the first start and stays as ``chats.json.bak``. The list is kept
in memory under a lock; the window, the bots and «Fluxion везде» may save from
different threads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

_ROLES = ("user", "assistant")
_VIEW_ROLES = ("user", "assistant", "banner")
_MAX_SESSIONS = 500
TITLE_LENGTH = 60
DEFAULT_TITLE = "Чат"

_lock = threading.RLock()
_cache: dict[str, list[dict]] = {}  # the database file -> its chats, oldest first


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
    """Replace every saved chat with *sessions* (clear, delete): one transaction."""
    with _lock:
        try:
            with _connection() as db:
                db.execute("DELETE FROM chats")
                db.executemany("INSERT INTO chats (id, updated, data) VALUES (?, ?, ?)",
                               [(s["id"], s["updated"], json.dumps(s, ensure_ascii=False)) for s in sessions])
        except (OSError, sqlite3.Error):
            pass
        _cache[str(db_path())] = sorted(sessions, key=lambda s: s["updated"])


def db_path() -> Path:
    """The chats: one row a chat in SQLite, next to the chats.json of the older versions."""
    return chats_path().with_name("chats.db")


def _connection() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path), timeout=30)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS chats (id TEXT PRIMARY KEY, updated REAL NOT NULL, data TEXT NOT NULL)")
    return db


def _read_json_file() -> list[dict]:
    """The chats of chats.json (versions up to 0.29), normalized; [] without the file."""
    try:
        data = json.loads(chats_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    sessions: list[dict] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                session = _normalize(item)
                if session is not None:
                    sessions.append(session)
    return sessions


def _migrate_json() -> list[dict]:
    """First start with the database: chats.json goes into it whole, the file stays as chats.json.bak."""
    sessions = _read_json_file()
    if not sessions:
        legacy = _migrate_legacy()
        return legacy or []
    _write(sessions)
    try:
        backup = chats_path().with_name(chats_path().name + ".bak")
        os.replace(chats_path(), backup)
    except OSError:
        pass
    return sessions


def _load() -> list[dict]:
    """All chats from the database (made from chats.json the first time), oldest first."""
    sessions: list[dict] = []
    if not db_path().is_file():
        sessions = _migrate_json()
    else:
        try:
            with _connection() as db:
                rows = db.execute("SELECT data FROM chats").fetchall()
        except (OSError, sqlite3.Error):
            rows = []
        for (data,) in rows:
            try:
                item = json.loads(data)
            except ValueError:
                continue
            session = _normalize(item) if isinstance(item, dict) else None
            if session is not None:
                sessions.append(session)
        if not sessions and not rows:
            sessions = _migrate_legacy() or []
    sessions.sort(key=lambda s: s["updated"])
    if len(sessions) > _MAX_SESSIONS:
        _forget([s["id"] for s in sessions[:-_MAX_SESSIONS]])
        sessions = sessions[-_MAX_SESSIONS:]
    return sessions


def _cached() -> list[dict]:
    key = str(db_path())
    if key not in _cache:
        _cache[key] = _load()
    return _cache[key]


def _forget(ids: list[str]) -> None:
    if not ids:
        return
    try:
        with _connection() as db:
            db.executemany("DELETE FROM chats WHERE id = ?", [(i,) for i in ids])
    except (OSError, sqlite3.Error):
        pass


def _put(session: dict) -> None:
    try:
        with _connection() as db:
            db.execute("INSERT OR REPLACE INTO chats (id, updated, data) VALUES (?, ?, ?)",
                       (session["id"], session["updated"], json.dumps(session, ensure_ascii=False)))
    except (OSError, sqlite3.Error):
        pass


def load_sessions() -> list[dict]:
    """All saved chats, oldest first; never raises. Read from the disk again (another window may have saved)."""
    with _lock:
        _cache[str(db_path())] = _load()
        return list(_cache[str(db_path())])


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

    Only this chat is written (one row), not the whole history. A chat renamed
    by hand keeps its name: the saved one wins over the title the window
    derives from the first question."""
    with _lock:
        old = _cached()
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
            _put(normalized)
        elif previous is not None:
            _forget([previous["id"]])
        sessions.sort(key=lambda s: s["updated"])
        if len(sessions) > _MAX_SESSIONS:
            _forget([s["id"] for s in sessions[:-_MAX_SESSIONS]])
            sessions = sessions[-_MAX_SESSIONS:]
        _cache[str(db_path())] = sessions
        return list(sessions)


def rename_session(session_id: str, title: str) -> list[dict]:
    """Give a chat its own name; an empty name returns to the first question."""
    with _lock:
        sessions = _cached()
        title = " ".join(title.split())[:TITLE_LENGTH]
        for s in sessions:
            if s["id"] == session_id:
                s["title"] = title or session_title(s["messages"])
                s["renamed"] = bool(title)
                _put(s)
        return list(sessions)


def delete_sessions(session_ids) -> list[dict]:
    with _lock:
        ids = set(session_ids)
        _forget(list(ids))
        sessions = [s for s in _cached() if s["id"] not in ids]
        _cache[str(db_path())] = sessions
        return list(sessions)


def delete_session(session_id: str) -> list[dict]:
    return delete_sessions([session_id])


def clear_sessions() -> list[dict]:
    """«Очистить всю историю»: the old files go too (chats.json, its copy, the single-chat file), or they
    would come back."""
    with _lock:
        _write([])
        for path in (history_path(), chats_path(), chats_path().with_name(chats_path().name + ".bak")):
            try:
                path.unlink()
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
