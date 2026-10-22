"""Schema migrations must upgrade pre-Alembic databases in place, without data loss."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.db import _alembic_config, upgrade_database
from app.services.secrets import (
    GMAIL_CLIENT_SECRET,
    GMAIL_REFRESH_TOKEN,
    get_secret_store,
    get_user_secret,
)


HEAD = ScriptDirectory.from_config(_alembic_config()).get_current_head()


def _legacy_engine(tmp_path: Path):
    """A database shaped like the old `create_all` output: baseline tables, no alembic_version."""

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    cfg = _alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "0001")
        conn.execute(text("DROP TABLE alembic_version"))
    return engine


def _seed_legacy_rows(engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (id, name, target_roles, target_locations, work_modes,"
                " skills, links, application_defaults, approval_top_n)"
                " VALUES (1, 'Lin', '[]', '[]', '[]', '[]', '{}', '{}', 5)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, user_id, kind, label, content) VALUES"
                " (1, 1, 'resume', 'Linh_Resume.pdf', 'base'),"
                " (2, 1, 'resume', 'Tailored resume — Engineer', 'tailored'),"
                " (3, 1, 'cover_letter', 'Cover letter — Engineer', 'cover')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO jobs"
                " (id, source, external_id, url, title, match_score, match_reason) VALUES"
                " (1, 'email_linkedin', 'a', 'https://x/a', 'A', 81,"
                "  'Strong overlap with Python.'),"
                " (2, 'email_linkedin', 'b', 'https://x/b', 'B', 73,"
                "  'Matched 2 role/skill keyword(s). Location aligns with Toronto.'),"
                " (3, 'email_linkedin', 'c', 'https://x/c', 'C', 65,"
                "  'Could not parse model score; defaulting to neutral.'),"
                " (4, 'email_linkedin', 'd', 'https://x/d', 'D', NULL, NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO applications"
                " (id, user_id, job_id, resume_doc_id, cover_doc_id, status)"
                " VALUES (7, 1, 1, 2, 3, 'approved')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO email_sync_states (id, user_id, provider, gmail_refresh_token)"
                " VALUES (1, 1, 'gmail', 'refresh-tok-123')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO app_config (key, value) VALUES"
                " ('gmail_client_id', 'cid'), ('gmail_client_secret', 'client-secret-xyz')"
            )
        )


def test_upgrade_legacy_database_keeps_rows_and_backfills(tmp_path: Path) -> None:
    engine = _legacy_engine(tmp_path)
    _seed_legacy_rows(engine)

    upgrade_database(engine)

    with engine.connect() as conn:
        counts = {
            table: conn.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in ("users", "documents", "jobs", "applications", "email_sync_states")
        }
        assert counts == {
            "users": 1,
            "documents": 3,
            "jobs": 4,
            "applications": 1,
            "email_sync_states": 1,
        }

        docs = dict(
            (row.id, (bool(row.is_base), row.application_id))
            for row in conn.execute(text("SELECT id, is_base, application_id FROM documents"))
        )
        assert docs == {1: (True, None), 2: (False, 7), 3: (False, 7)}

        sources = dict(conn.execute(text("SELECT id, match_source FROM jobs")).fetchall())
        assert sources == {1: "llm", 2: "heuristic", 3: "heuristic", 4: None}

        sync_cols = {c["name"] for c in inspect(conn).get_columns("email_sync_states")}
        assert "gmail_refresh_token" not in sync_cols
        assert "imap_app_password" not in sync_cols
        config_keys = {row[0] for row in conn.execute(text("SELECT key FROM app_config"))}
        assert config_keys == {"gmail_client_id"}

        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert version == HEAD

    assert get_user_secret(1, GMAIL_REFRESH_TOKEN) == "refresh-tok-123"
    assert get_secret_store().get(GMAIL_CLIENT_SECRET) == "client-secret-xyz"


def test_upgrade_is_idempotent(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}")
    upgrade_database(engine)
    upgrade_database(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == HEAD


def test_upgrade_refuses_partial_legacy_database(tmp_path: Path) -> None:
    engine = _legacy_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE email_sync_states"))
    with pytest.raises(RuntimeError, match="email_sync_states"):
        upgrade_database(engine)
