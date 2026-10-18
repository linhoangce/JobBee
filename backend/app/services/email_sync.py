"""Helpers for persisting email inbox sync cursors."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import EmailSyncState


def get_or_create_email_sync_state(session: Session, user_id: int) -> EmailSyncState:
    """Return the user's email sync row, creating it on first call."""

    state = session.execute(
        select(EmailSyncState).where(EmailSyncState.user_id == user_id)
    ).scalar_one_or_none()
    if state is None:
        state = EmailSyncState(user_id=user_id)
        session.add(state)
        session.commit()
        session.refresh(state)
    return state
