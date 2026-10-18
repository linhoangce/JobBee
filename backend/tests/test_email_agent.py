"""Email agent ingest pipeline tests."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.email_agent import (
    SyncedJobAlert,
    alert_to_listing,
    external_id_from_alert,
    run_email_sync,
)
from app.agents.jd_fetch_agent import JdFetchResult
from app.config import Settings
from app.db import SessionLocal
from app.main import app
from app.models import Job, User
from app.services.email_client import RawEmailMessage
from app.services.email_connection import UserEmailConnection


def _app_settings() -> Settings:
    return Settings(
        gmail_client_id="cid",
        gmail_client_secret="secret",
        gmail_oauth_redirect_uri="http://127.0.0.1:8000/api/email/gmail/callback",
    )


def _gmail_connection() -> UserEmailConnection:
    return UserEmailConnection(
        provider="gmail",
        connected_email="user@gmail.com",
        gmail_refresh_token="refresh",
    )


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
    listing = alert_to_listing(alert)
    assert listing.source == "email_linkedin"
    assert listing.external_id == "5000000001"
    assert listing.description == "Python role"


def test_email_sync_api_upserts_jobs() -> None:
    fake_messages = [
        RawEmailMessage(
            provider_message_id="abc",
            subject="New jobs matching Python",
            sender="jobs-listings@linkedin.com",
            received_at=datetime.now(timezone.utc),
            snippet="Role at Acme",
            body=(
                '<a href="https://www.linkedin.com/jobs/view/5000000002">Platform Engineer</a>'
                "<p>Beta Corp · Remote</p>"
            ),
        )
    ]
    fake_updates = {"provider": "gmail", "gmail_history_id": "9912345"}

    mock_client = MagicMock()
    mock_client.fetch_new_messages.return_value = (fake_messages, fake_updates)

    with patch("app.api.email.get_settings", return_value=_app_settings()):
        with patch("app.api.email.user_is_connected", return_value=True):
            with patch("app.api.email.connection_from_state", return_value=_gmail_connection()):
                with patch("app.api.email._build_email_client_for_state", return_value=mock_client):
                    with patch(
                        "app.services.job_ingest.score_job",
                        new_callable=AsyncMock,
                    ) as score_job:
                        from app.agents.match_agent import MatchResult

                        score_job.return_value = MatchResult(
                            score=75.0, reason="Good fit."
                        )
                        with TestClient(app) as client:
                            client.put(
                                "/api/profile",
                                json={
                                    "name": "Lin",
                                    "target_roles": ["Backend engineer"],
                                    "skills": ["python"],
                                },
                            )
                            resp = client.post(
                                "/api/email/sync",
                                json={"score": True, "queue_for_approval": False},
                            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["messages_scanned"] == 1
    assert body["jobs_found"] == 1
    assert body["jobs_upserted"] == 1
    assert body["jobs_scored"] == 1
    assert body["jobs"][0]["alert_source"] == "email_linkedin"

    with TestClient(app) as client:
        jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    feed = jobs.json()
    assert any(j["source"] == "email_linkedin" for j in feed)


@pytest.mark.asyncio
async def test_run_email_sync_enriches_short_descriptions_when_enabled() -> None:
    enriched = "Senior backend engineer role. " * 20
    fake_messages = [
        RawEmailMessage(
            provider_message_id="jd-1",
            subject="New jobs matching Python",
            sender="jobs-listings@linkedin.com",
            received_at=datetime.now(timezone.utc),
            snippet="Short snippet",
            body=(
                '<a href="https://www.linkedin.com/jobs/view/5000000010">Platform Engineer</a>'
                "<p>Beta Corp · Remote</p>"
            ),
        )
    ]

    settings = _app_settings()
    settings.email_enrich_jd = True

    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend engineer"], "skills": ["python"]},
        )

    with SessionLocal() as session:
        user = session.execute(select(User)).scalar_one()
        with patch("app.agents.email_agent.get_settings", return_value=settings):
            with patch(
                "app.agents.email_agent.fetch_job_description",
                new_callable=AsyncMock,
            ) as fetch_jd:
                fetch_jd.return_value = JdFetchResult(
                    page_url="https://www.linkedin.com/jobs/view/5000000010",
                    text=enriched,
                )
                with patch(
                    "app.services.job_ingest.score_job",
                    new_callable=AsyncMock,
                ) as score_job:
                    from app.agents.match_agent import MatchResult

                    score_job.return_value = MatchResult(score=70.0, reason="Good fit.")
                    result = await run_email_sync(session, user, fake_messages, score_jobs=True)

        session.refresh(user)
        job = session.execute(select(Job).where(Job.external_id == "5000000010")).scalar_one()
        assert len(job.description or "") > 200
        assert job.description == enriched
        fetch_jd.assert_awaited_once()
        assert result.jobs_scored == 1


@pytest.mark.asyncio
async def test_run_email_sync_skips_enrichment_when_disabled() -> None:
    fake_messages = [
        RawEmailMessage(
            provider_message_id="jd-2",
            subject="New jobs matching Python",
            sender="jobs-listings@linkedin.com",
            received_at=datetime.now(timezone.utc),
            snippet="Short snippet",
            body=(
                '<a href="https://www.linkedin.com/jobs/view/5000000011">Data Engineer</a>'
                "<p>Gamma Corp · Remote</p>"
            ),
        )
    ]

    settings = _app_settings()
    settings.email_enrich_jd = False

    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend engineer"], "skills": ["python"]},
        )

    with SessionLocal() as session:
        user = session.execute(select(User)).scalar_one()
        with patch("app.agents.email_agent.get_settings", return_value=settings):
            with patch(
                "app.agents.email_agent.fetch_job_description",
                new_callable=AsyncMock,
            ) as fetch_jd:
                with patch(
                    "app.services.job_ingest.score_job",
                    new_callable=AsyncMock,
                ) as score_job:
                    from app.agents.match_agent import MatchResult

                    score_job.return_value = MatchResult(score=65.0, reason="OK")
                    await run_email_sync(session, user, fake_messages, score_jobs=True)

        fetch_jd.assert_not_awaited()
