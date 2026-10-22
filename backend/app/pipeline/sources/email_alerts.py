"""LinkedIn / Indeed job-alert emails from the connected inbox (Gmail or IMAP)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ...agents.email_agent import parse_inbox_messages
from ...config import get_settings
from ...models import User
from ...services.app_config import resolve_gmail_credentials
from ...services.email_client import EmailClientError, build_email_client
from ...services.email_connection import (
    clear_email_connection,
    connection_from_state,
    user_is_connected,
)
from ...services.email_sync import get_or_create_email_sync_state
from .base import Candidate, SourceError

_CURSOR_FIELDS = ("provider", "gmail_history_id", "imap_uid_validity", "imap_last_uid")
_DEAD_CONNECTION = ("deleted_client", "invalid_grant", "unauthorized_client", "invalid_client")


def inbox_connected(session: Session, user: User) -> bool:
    return user_is_connected(get_or_create_email_sync_state(session, user.id))


def disconnect_if_dead(session: Session, state, error: EmailClientError) -> str | None:
    """Drop a connection Google no longer accepts; returns the message to show, else None."""

    if not any(marker in str(error) for marker in _DEAD_CONNECTION):
        return None
    # The grant or the Google sign-in app is gone for good: stop retrying.
    email = state.connected_email or "your inbox"
    clear_email_connection(state)
    session.commit()
    return (
        f"Disconnected {email}: Google no longer accepts JobBee's sign-in "
        "(the sign-in app was deleted or access was revoked). Reconnect it in "
        "Settings → Where to look."
    )


def inbox_client(session: Session, user_id: int):
    """(client, sync state) for the user's connected inbox, or (None, state)."""

    state = get_or_create_email_sync_state(session, user_id)
    connection = connection_from_state(state) if user_is_connected(state) else None
    if connection is None:
        return None, state
    credentials = resolve_gmail_credentials(session)
    client = build_email_client(
        connection,
        app_settings=get_settings(),
        gmail_client_id=credentials.client_id if credentials else None,
        gmail_client_secret=credentials.client_secret if credentials else None,
    )
    return client, state


async def fetch_alerts(session: Session, user: User) -> list[Candidate]:
    """New job alerts since the last sync; advances the inbox cursor."""

    state = get_or_create_email_sync_state(session, user.id)
    try:
        client, state = inbox_client(session, user.id)
        if client is None:
            return []
        # The Gmail and IMAP clients are synchronous; keep them off the event loop.
        messages, updates = await asyncio.to_thread(client.fetch_new_messages, cursor=state)
    except EmailClientError as e:
        raise SourceError(disconnect_if_dead(session, state, e) or f"Inbox: {e}") from None
    for key in _CURSOR_FIELDS:
        if key in updates:
            setattr(state, key, updates[key])
    state.last_sync_at = datetime.now(timezone.utc)
    session.commit()

    return [
        Candidate(
            url=alert.url,
            source="email",
            title=alert.title,
            company=alert.company,
            location=alert.location,
            snippet=alert.snippet,
        )
        for alert in parse_inbox_messages(messages).jobs
    ]
