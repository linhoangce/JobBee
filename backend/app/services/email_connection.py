"""Per-user inbox connection helpers.

Connection metadata lives in SQLite; the Gmail refresh token and IMAP app
password live in the secret store (`services/secrets.py`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import Settings
from ..models import EmailSyncState
from .app_config import gmail_oauth_configured
from .secrets import GMAIL_REFRESH_TOKEN, IMAP_APP_PASSWORD, get_user_secret, set_user_secret


@dataclass(frozen=True)
class UserEmailConnection:
    provider: str
    connected_email: str | None
    gmail_refresh_token: str | None = None
    imap_host: str | None = None
    imap_user: str | None = None
    imap_app_password: str | None = None
    imap_folder: str = "INBOX"


def user_is_connected(state: EmailSyncState) -> bool:
    if not state.provider or state.provider == "disabled":
        return False
    if state.provider == "gmail":
        return get_user_secret(state.user_id, GMAIL_REFRESH_TOKEN) is not None
    if state.provider == "imap":
        return bool(
            (state.imap_host or "").strip()
            and (state.imap_user or "").strip()
            and get_user_secret(state.user_id, IMAP_APP_PASSWORD) is not None
        )
    return False


def connection_from_state(state: EmailSyncState) -> UserEmailConnection | None:
    if not user_is_connected(state):
        return None
    assert state.provider in ("gmail", "imap")
    return UserEmailConnection(
        provider=state.provider,
        connected_email=state.connected_email,
        gmail_refresh_token=get_user_secret(state.user_id, GMAIL_REFRESH_TOKEN),
        imap_host=state.imap_host,
        imap_user=state.imap_user,
        imap_app_password=get_user_secret(state.user_id, IMAP_APP_PASSWORD),
        imap_folder=(state.imap_folder or "INBOX").strip() or "INBOX",
    )


def clear_email_connection(state: EmailSyncState) -> None:
    state.provider = None
    state.connected_email = None
    state.connected_at = None
    set_user_secret(state.user_id, GMAIL_REFRESH_TOKEN, None)
    state.gmail_history_id = None
    state.imap_host = None
    state.imap_user = None
    set_user_secret(state.user_id, IMAP_APP_PASSWORD, None)
    state.imap_folder = "INBOX"
    state.imap_uid_validity = None
    state.imap_last_uid = None
    state.oauth_state = None


def save_gmail_connection(
    state: EmailSyncState,
    *,
    refresh_token: str,
    connected_email: str | None,
) -> None:
    clear_email_connection(state)
    state.provider = "gmail"
    set_user_secret(state.user_id, GMAIL_REFRESH_TOKEN, refresh_token)
    state.connected_email = connected_email
    state.connected_at = datetime.now(timezone.utc)


def save_imap_connection(
    state: EmailSyncState,
    *,
    host: str,
    user: str,
    app_password: str,
    folder: str = "INBOX",
) -> None:
    clear_email_connection(state)
    state.provider = "imap"
    state.imap_host = host.strip()
    state.imap_user = user.strip()
    set_user_secret(state.user_id, IMAP_APP_PASSWORD, app_password)
    state.imap_folder = folder.strip() or "INBOX"
    state.connected_email = state.imap_user
    state.connected_at = datetime.now(timezone.utc)


def mailbox_label(state: EmailSyncState) -> str | None:
    if state.provider == "gmail":
        return "me"
    if state.provider == "imap":
        return state.imap_folder or "INBOX"
    return None


def status_provider(state: EmailSyncState) -> str:
    if user_is_connected(state):
        assert state.provider
        return state.provider
    return "disabled"


def gmail_oauth_ready(settings: Settings, session: Session | None = None) -> bool:
    if settings.gmail_oauth_available():
        return True
    if session is not None:
        return gmail_oauth_configured(session)
    return False
