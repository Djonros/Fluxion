"""The journal of «Личный помощник»: what the connectors saw, in SQLite on this computer.

    %LOCALAPPDATA%\\Fluxion\\data\\assistant\\journal.db

One line is an event (``connector.Event``): its time, connector, kind, a title, what it
is about (``ref``) and a short detail — never the content of a file or a letter. The
full-text index (FTS5, as «Архив КД») finds lines by words in Russian and English.
Lines older than the retention period are deleted (``purge``); the user may delete
any day or everything from the window «Что знает Fluxion».
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .connector import Event

DAY = 24 * 60 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    time REAL NOT NULL,
    connector TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    duration REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS events_time ON events(time);
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(
    title, ref, detail, content='events', content_rowid='id', tokenize='unicode61');
CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
    INSERT INTO events_fts(rowid, title, ref, detail) VALUES (new.id, new.title, new.ref, new.detail);
END;
CREATE TRIGGER IF NOT EXISTS events_ad AFTER DELETE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, ref, detail)
    VALUES ('delete', old.id, old.title, old.ref, old.detail);
END;
CREATE TRIGGER IF NOT EXISTS events_au AFTER UPDATE ON events BEGIN
    INSERT INTO events_fts(events_fts, rowid, title, ref, detail)
    VALUES ('delete', old.id, old.title, old.ref, old.detail);
    INSERT INTO events_fts(rowid, title, ref, detail) VALUES (new.id, new.title, new.ref, new.detail);
END;
"""


def default_path() -> Path:
    from core.app_paths import data_dir

    return data_dir() / "assistant" / "journal.db"


def start_of_day(when: float | None = None, days_back: int = 0) -> float:
    day = datetime.fromtimestamp(time.time() if when is None else when).replace(
        hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_back)
    return day.timestamp()


def _fts_query(text: str) -> str:
    """Words of the user as an FTS5 query: every word, as a prefix ("модел" finds "модели")."""
    words = [w for w in "".join(ch if ch.isalnum() else " " for ch in text.lower()).split() if w]
    return " ".join(f'"{w}"*' for w in words)


class Journal:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path else default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── writing ──
    def add(self, event: Event) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events(time, connector, kind, title, ref, detail, duration) VALUES (?,?,?,?,?,?,?)",
                (event.time, event.connector, event.kind, event.title, event.ref, event.detail, event.duration))
            self._db.commit()
            return int(cur.lastrowid)

    def extend(self, row_id: int, duration: float) -> None:
        """An activity interval goes on: its line gets the new length."""
        with self._lock:
            self._db.execute("UPDATE events SET duration=? WHERE id=?", (duration, row_id))
            self._db.commit()

    def delete(self, since: float | None = None, until: float | None = None, connector: str = "") -> int:
        where, args = self._where(since, until, connector)
        with self._lock:
            cur = self._db.execute(f"DELETE FROM events {where}", args)
            self._db.commit()
            return cur.rowcount

    def purge(self, retention_days: int, now: float | None = None) -> int:
        return self.delete(until=(time.time() if now is None else now) - retention_days * DAY)

    # ── reading ──
    @staticmethod
    def _where(since, until, connector, kinds=()) -> tuple[str, list]:
        parts, args = [], []
        if since is not None:
            parts.append("events.time >= ?")
            args.append(since)
        if until is not None:
            parts.append("events.time < ?")
            args.append(until)
        if connector:
            parts.append("events.connector = ?")
            args.append(connector)
        if kinds:
            parts.append(f"events.kind IN ({','.join('?' * len(kinds))})")
            args.extend(kinds)
        return ("WHERE " + " AND ".join(parts)) if parts else "", args

    def events(self, since: float | None = None, until: float | None = None, connector: str = "",
               kinds: tuple[str, ...] = (), query: str = "", limit: int = 500) -> list[Event]:
        where, args = self._where(since, until, connector, kinds)
        fts = _fts_query(query) if query else ""
        if fts:
            where = (where + " AND " if where else "WHERE ") + "events.id IN (SELECT rowid FROM events_fts WHERE events_fts MATCH ?)"
            args.append(fts)
        sql = f"SELECT * FROM events {where} ORDER BY events.time DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(sql, [*args, int(limit)]).fetchall()
        return [Event(connector=r["connector"], kind=r["kind"], title=r["title"], ref=r["ref"],
                      detail=r["detail"], time=r["time"], duration=r["duration"]) for r in rows]

    def count(self, connector: str = "") -> int:
        where, args = self._where(None, None, connector)
        with self._lock:
            return int(self._db.execute(f"SELECT COUNT(*) FROM events {where}", args).fetchone()[0])
