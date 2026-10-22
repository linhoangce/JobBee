"""SQLite + SQLAlchemy session plumbing."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect
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


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_BASELINE_REVISION = "0001"
_BASELINE_TABLES = {"users", "app_config", "email_sync_states", "documents", "jobs", "applications"}


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record) -> None:
    """WAL + busy timeout so the API and the apply worker can share the file."""

    import sqlite3

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _alembic_config():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


def init_db() -> None:
    upgrade_database(engine)


def upgrade_database(target: Engine) -> None:
    """Bring the database schema to the latest Alembic revision.

    Databases created before Alembic (via `create_all`) match the 0001
    baseline, so they are stamped at 0001 and then upgraded in place —
    existing rows are never dropped.
    """

    from alembic import command

    cfg = _alembic_config()
    with target.begin() as connection:
        cfg.attributes["connection"] = connection
        tables = set(inspect(connection).get_table_names())
        if tables & _BASELINE_TABLES and "alembic_version" not in tables:
            missing = _BASELINE_TABLES - tables
            if missing:
                raise RuntimeError(
                    "Database predates migrations and is missing tables "
                    f"{sorted(missing)}; restore a backup or start from an empty database."
                )
            logger.info("Stamping pre-migration database at baseline revision %s", _BASELINE_REVISION)
            command.stamp(cfg, _BASELINE_REVISION)
        command.upgrade(cfg, "head")


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
