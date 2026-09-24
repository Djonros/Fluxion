"""Web cache: SQLite-backed with per-URL TTL."""
from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_DB = "data/web_cache.db"


class WebCache:
    """Simple SQLite cache mapping URL → (title, content, timestamp)."""

    def __init__(self, db_path: str = _DEFAULT_DB, default_ttl: int = 86400):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache (
                    url        TEXT PRIMARY KEY,
                    title      TEXT NOT NULL DEFAULT '',
                    content    TEXT NOT NULL DEFAULT '',
                    fetched_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path))

    def get(self, url: str, ttl: int | None = None) -> tuple[str, str] | None:
        """Return (title, content) if cached and fresh, else None."""
        effective_ttl = ttl if ttl is not None else self.default_ttl
        with self._connect() as conn:
            row = conn.execute(
                "SELECT title, content, fetched_at FROM cache WHERE url = ?", (url,)
            ).fetchone()

        if row is None:
            return None
        title, content, fetched_at = row
        age = time.time() - fetched_at
        if age > effective_ttl:
            return None
        return title, content

    def set(self, url: str, title: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO cache (url, title, content, fetched_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    title = excluded.title,
                    content = excluded.content,
                    fetched_at = excluded.fetched_at
                """,
                (url, title, content, time.time()),
            )
            conn.commit()

    def clear(self) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache")
            conn.commit()

    def count(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM cache").fetchone()
            return row[0] if row else 0

    def prune(self, max_age: int | None = None) -> int:
        """Delete entries older than max_age seconds. Returns count deleted."""
        effective = max_age if max_age is not None else self.default_ttl
        cutoff = time.time() - effective
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cache WHERE fetched_at < ?", (cutoff,))
            conn.commit()
            return cur.rowcount
