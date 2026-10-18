"""SQLite + SQLAlchemy session plumbing."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


_settings = get_settings()
_engine_kwargs: dict = {"future": True}
if _settings.resolved_database_url().startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(_settings.resolved_database_url(), **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def _missing_tables() -> set[str]:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    return {table.name for table in Base.metadata.sorted_tables} - existing


def _schema_is_stale() -> bool:
    """Return True iff any existing table is missing columns the models declare.

    We intentionally do NOT flag the reverse case (extra columns in the DB) so a
    forward-compatible binary can read an older DB without nuking it. This is a
    dev-only convenience; for production we'd switch to Alembic.
    """

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            # Missing tables are handled by create_all, not a reason to drop.
            continue
        existing_cols = {c["name"] for c in inspector.get_columns(table.name)}
        declared_cols = {c.name for c in table.columns}
        missing = declared_cols - existing_cols
        if missing:
            logger.warning(
                "Schema drift on table %r: missing columns %s. Will drop and recreate.",
                table.name,
                sorted(missing),
            )
            return True
    return False


def init_db() -> None:
    """Create tables; in dev, drop+recreate if existing tables are missing columns."""

    # Import here so SQLAlchemy sees the model classes.
    from . import models  # noqa: F401

    missing = _missing_tables()
    if missing:
        logger.info("Creating missing tables: %s", sorted(missing))

    if _schema_is_stale():
        Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency that yields a request-scoped session."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


UPLOADS_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"


def get_uploads_dir() -> Path:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOADS_DIR
