"""Job-alert emails: parsing and the inbox source used by job search."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.agents.email_agent import SyncedJobAlert, external_id_from_alert
from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.models import EmailSyncState
from app.pipeline.sources.email_alerts import fetch_alerts
from app.services.email_client import RawEmailMessage
from app.services.email_connection import save_gmail_connection
from app.services.email_sync import get_or_create_email_sync_state


def test_external_id_from_linkedin_alert() -> None:
    alert = SyncedJobAlert(
        title="Backend Engineer",
        company="Acme",
        location="Remote",
        url="https://www.linkedin.com/jobs/view/5000000001",
        snippet="Python role",
        alert_source="email_linkedin",
        email_subject="New jobs",
        email_sender="jobs-listings@linkedin.com",
    )
    assert external_id_from_alert(alert) == "5000000001"


@pytest.mark.asyncio
async def test_inbox_source_returns_alerts_and_advances_cursor() -> None:
    message = RawEmailMessage(
        provider_message_id="abc",
        subject="New jobs matching Python",
        sender="jobs-listings@linkedin.com",
        received_at=datetime.now(timezone.utc),
        snippet="Role at Acme",
        body=(
            '<a href="https://www.linkedin.com/jobs/view/5000000001">Backend Engineer</a>'
            "<p>Acme Corp · Remote</p>"
        ),
    )
    client = MagicMock()
    client.fetch_new_messages.return_value = (
        [message],
        {"provider": "gmail", "gmail_history_id": "9912345"},
    )
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        state = get_or_create_email_sync_state(session, user.id)
        save_gmail_connection(state, refresh_token="tok", connected_email="u@gmail.com")
        session.commit()
        with patch("app.pipeline.sources.email_alerts.build_email_client", return_value=client):
            found = await fetch_alerts(session, user)
        assert [c.source for c in found] == ["email"]
        assert found[0].url == "https://www.linkedin.com/jobs/view/5000000001"
        assert found[0].title == "Backend Engineer"
        state = session.query(EmailSyncState).filter_by(user_id=user.id).one()
        assert state.gmail_history_id == "9912345" and state.last_sync_at is not None
        state.provider = None
        state.connected_email = None
        session.commit()


@pytest.mark.asyncio
async def test_dead_google_sign_in_disconnects_instead_of_failing_every_search() -> None:
    from app.pipeline.sources.base import SourceError
    from app.services.email_client import EmailClientError

    client = MagicMock()
    client.fetch_new_messages.side_effect = EmailClientError(
        'Gmail token refresh failed: 401 {"error": "deleted_client"}'
    )
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        state = get_or_create_email_sync_state(session, user.id)
        save_gmail_connection(state, refresh_token="tok", connected_email="u@gmail.com")
        session.commit()
        with patch("app.pipeline.sources.email_alerts.build_email_client", return_value=client):
            with pytest.raises(SourceError, match="Disconnected u@gmail.com"):
                await fetch_alerts(session, user)
            assert await fetch_alerts(session, user) == []  # no longer connected: skipped
