"""Database engine, session factory, and base model.

Uses SQLAlchemy 2.0 sync API with PostgreSQL.
Database URL is read from FLUXION_DB_URL env var
(default: sqlite for development when PostgreSQL is not available).
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


def _get_database_url() -> str:
    """Return database URL from env, fallback to SQLite for local dev."""
    url = os.environ.get("FLUXION_DB_URL", "")
    if url:
        return url
    return "sqlite:///./data/fluxion.db"


DATABASE_URL = _get_database_url()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args, echo=False)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """Declarative base for all models."""
    pass


def get_db() -> Session:
    """FastAPI dependency that yields a database session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_sqlite_dir(url: str) -> None:
    """SQLite creates the file but not its folder: on a fresh checkout (data/
    is not in git) the default ./data/fluxion.db could not be opened."""
    if not url.startswith("sqlite:///"):
        return
    path = url[len("sqlite:///"):].split("?", 1)[0]
    if path and path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def init_db() -> None:
    """Create all tables. Call on application startup."""
    _ensure_sqlite_dir(DATABASE_URL)
    Base.metadata.create_all(bind=engine)
