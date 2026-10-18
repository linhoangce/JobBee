"""Job ingest helper tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.match_agent import MatchResult
from app.db import SessionLocal
from app.main import app
from app.models import Job, User
from app.services.job_ingest import _get_base_resume, score_jobs_batch


def test_get_base_resume_prefers_non_tailored_copy() -> None:
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend"], "skills": ["python"]},
        )
        client.post(
            "/api/documents/text",
            json={
                "kind": "resume",
                "text": "Tailored body",
                "label": "Tailored resume — Acme",
            },
        )
        client.post(
            "/api/documents/text",
            json={
                "kind": "resume",
                "text": "Base body",
                "label": "Uploaded resume",
            },
        )

    with SessionLocal() as session:
        user = session.execute(select(User)).scalar_one()
        resume = _get_base_resume(session, user)
        assert resume is not None
        assert resume.label == "Uploaded resume"


@pytest.mark.asyncio
async def test_score_jobs_batch_with_multiple_resumes() -> None:
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend"], "skills": ["python"]},
        )
        client.post(
            "/api/documents/text",
            json={"kind": "resume", "text": "First resume", "label": "v1"},
        )
        client.post(
            "/api/documents/text",
            json={"kind": "resume", "text": "Second resume", "label": "v2"},
        )

    with SessionLocal() as session:
        user = session.execute(select(User)).scalar_one()
        job = Job(
            source="email_linkedin",
            external_id="123",
            url="https://www.linkedin.com/jobs/view/123",
            title="Engineer",
            company="Acme",
            location="Remote",
            description="Python",
        )
        session.add(job)
        session.commit()
        session.refresh(job)

        with patch("app.services.job_ingest.score_job", new_callable=AsyncMock) as score:
            score.return_value = MatchResult(score=80.0, reason="Good fit")
            jobs_scored, warnings = await score_jobs_batch(session, user, [job])

        assert jobs_scored == 1
        assert warnings == []
        assert job.match_score == 80.0
