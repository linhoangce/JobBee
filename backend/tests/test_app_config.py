"""Tests for app-level config storage."""

from __future__ import annotations

from app.services.app_config import (
    gmail_oauth_configured,
    resolve_gmail_credentials,
    save_gmail_oauth_setup,
)
from app.config import get_settings
from app.db import SessionLocal


def test_save_and_resolve_gmail_credentials_from_db(monkeypatch) -> None:
    monkeypatch.setenv("GMAIL_CLIENT_ID", "")
    monkeypatch.setenv("GMAIL_CLIENT_SECRET", "")
    get_settings.cache_clear()
    with SessionLocal() as session:
        save_gmail_oauth_setup(
            session,
            client_id="stored-id.apps.googleusercontent.com",
            client_secret="stored-secret",
        )
        session.commit()

        creds = resolve_gmail_credentials(session)
        assert creds is not None
        assert creds.client_id == "stored-id.apps.googleusercontent.com"
        assert creds.client_secret == "stored-secret"
        assert gmail_oauth_configured(session)
    get_settings.cache_clear()
