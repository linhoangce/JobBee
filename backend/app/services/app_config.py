"""Local app-level config stored in SQLite (one-time setup the user does in the UI)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppConfig

GMAIL_CLIENT_ID_KEY = "gmail_client_id"
GMAIL_CLIENT_SECRET_KEY = "gmail_client_secret"


def get_config_value(session: Session, key: str) -> str | None:
    row = session.get(AppConfig, key)
    if row is None:
        return None
    value = (row.value or "").strip()
    return value or None


def set_config_value(session: Session, key: str, value: str) -> None:
    cleaned = value.strip()
    row = session.get(AppConfig, key)
    if row is None:
        session.add(AppConfig(key=key, value=cleaned))
    else:
        row.value = cleaned


@dataclass(frozen=True)
class GmailOAuthCredentials:
    client_id: str
    client_secret: str
    redirect_uri: str


def resolve_gmail_credentials(session: Session) -> GmailOAuthCredentials | None:
    """Return OAuth app credentials from `.env` or the in-app setup wizard."""

    from ..config import get_settings

    settings = get_settings()
    client_id = settings.gmail_client_id.strip()
    client_secret = settings.gmail_client_secret.strip()

    if not client_id:
        client_id = get_config_value(session, GMAIL_CLIENT_ID_KEY) or ""
    if not client_secret:
        client_secret = get_config_value(session, GMAIL_CLIENT_SECRET_KEY) or ""

    if not client_id or not client_secret:
        return None

    return GmailOAuthCredentials(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=settings.gmail_oauth_redirect_uri.strip(),
    )


def save_gmail_oauth_setup(session: Session, *, client_id: str, client_secret: str) -> None:
    set_config_value(session, GMAIL_CLIENT_ID_KEY, client_id)
    set_config_value(session, GMAIL_CLIENT_SECRET_KEY, client_secret)


def gmail_oauth_configured(session: Session) -> bool:
    return resolve_gmail_credentials(session) is not None
