"""Alembic environment.

Runs both from the CLI (`alembic upgrade head` in `backend/`) and from
`app.db.init_db()`, which passes its own connection via `config.attributes`.
SQLite needs batch mode so ALTERs become table copies.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _configure_and_run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().resolved_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure_and_run(connection)
        return

    engine = create_engine(get_settings().resolved_database_url())
    with engine.connect() as conn:
        _configure_and_run(conn)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
