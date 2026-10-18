"""Tailor + apply pipeline tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.apply_agent import ApplyPrepareResult
from app.agents.sources.base import RawJobListing
from app.agents.tailor_agent import TailorResult
from app.db import SessionLocal
from app.main import app
from app.models import Application, ApplicationStatus


@pytest.fixture
def seeded_jobs():
    sample = [
        RawJobListing(
            source="remoteok",
            external_id="job-a",
            url="https://example.com/a",
            title="Backend Engineer",
            company="Acme",
            location="Remote",
            description="Python backend role.",
            tags=["python"],
        ),
    ]

    async def fake_remoteok(*, query=None, limit=50):
        return sample

    async def fake_remotive(*, query=None, limit=50):
        return []

    with (
        patch("app.agents.search_agent.get_sources") as gs,
        patch("app.services.job_ingest.score_job", new_callable=AsyncMock) as score,
    ):
        gs.return_value = {"remoteok": fake_remoteok, "remotive": fake_remotive}
        from app.agents.match_agent import MatchResult

        async def fake_score(job, user, **kwargs):
            return MatchResult(score=90.0, reason="Strong fit")

        score.side_effect = fake_score
        yield


def _reset_and_seed(client: TestClient) -> int:
    with SessionLocal() as session:
        for app_row in session.execute(select(Application)).scalars():
            app_row.status = ApplicationStatus.matched
        session.commit()

    client.put(
        "/api/profile",
        json={
            "name": "Lin",
            "email": "lin@example.com",
            "target_roles": ["Backend engineer"],
            "skills": ["python"],
            "approval_top_n": 1,
        },
    )
    client.post("/api/documents/text", json={"kind": "resume", "text": "Lin Ho\nBackend engineer\nPython, FastAPI"})
    client.post("/api/jobs/search", json={"score": True, "queue_for_approval": True})
    pending = client.get("/api/approvals").json()["items"]
    app_id = pending[0]["id"]
    client.post(f"/api/approvals/{app_id}/approve")
    return app_id


def test_tailor_creates_linked_documents(seeded_jobs) -> None:
    tailor_result = TailorResult(
        resume_text="Lin Ho\nTailored backend resume with Python and FastAPI experience across multiple projects.",
        cover_text="Dear Acme hiring team,\n\nI am excited to apply for the Backend Engineer role.\n\nSincerely,\nLin",
    )
    with (
        TestClient(app) as client,
        patch(
            "app.services.tailoring.tailor_for_job",
            new_callable=AsyncMock,
            return_value=tailor_result,
        ),
    ):
        app_id = _reset_and_seed(client)
        resp = client.post(f"/api/applications/{app_id}/tailor")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["application"]["resume_doc_id"] is not None
    assert body["application"]["cover_doc_id"] is not None
    assert body["application"]["resume_preview"]
    assert body["application"]["cover_preview"]


def test_prepare_apply_requires_tailored_docs(seeded_jobs) -> None:
    with TestClient(app) as client:
        app_id = _reset_and_seed(client)
        resp = client.post(f"/api/applications/{app_id}/prepare-apply")
    assert resp.status_code == 400
    assert "Tailor" in resp.json()["detail"]


def test_prepare_and_confirm_submit(seeded_jobs) -> None:
    tailor_result = TailorResult(
        resume_text="Lin Ho\nTailored backend resume with Python and FastAPI experience across multiple projects.",
        cover_text="Dear Acme hiring team,\n\nI am excited to apply for the Backend Engineer role.\n\nSincerely,\nLin",
    )
    prepare_result = ApplyPrepareResult(
        page_url="https://example.com/a",
        fields_filled=["name", "email"],
        screenshot_path=None,
        submit_selector='button[type="submit"]',
        warnings=["Test warning"],
    )
    with (
        TestClient(app) as client,
        patch(
            "app.services.tailoring.tailor_for_job",
            new_callable=AsyncMock,
            return_value=tailor_result,
        ),
        patch(
            "app.services.apply.prepare_application_form",
            new_callable=AsyncMock,
            return_value=prepare_result,
        ),
        patch(
            "app.services.apply.confirm_application_submit",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
    ):
        app_id = _reset_and_seed(client)
        client.post(f"/api/applications/{app_id}/tailor")
        prepared = client.post(f"/api/applications/{app_id}/prepare-apply")
        assert prepared.status_code == 200, prepared.text
        assert prepared.json()["application"]["apply_prepared"] is True

        confirmed = client.post(f"/api/applications/{app_id}/confirm-submit")
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["application"]["status"] == "submitted"
        assert confirmed.json()["application"]["submitted_at"] is not None


def test_list_pipeline_excludes_pending(seeded_jobs) -> None:
    with TestClient(app) as client:
        _reset_and_seed(client)
        resp = client.get("/api/applications")
    assert resp.status_code == 200
    body = resp.json()
    assert body["summary"]["approved"] == 1
    assert len(body["items"]) == 1
    assert body["items"][0]["status"] == "approved"
