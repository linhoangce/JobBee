"""Approval queue tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.agents.sources.base import RawJobListing
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
            description="Python backend.",
            tags=["python"],
        ),
        RawJobListing(
            source="remotive",
            external_id="job-b",
            url="https://example.com/b",
            title="Platform Engineer",
            company="Beta",
            location="Remote",
            description="Kubernetes.",
            tags=["devops"],
        ),
        RawJobListing(
            source="remoteok",
            external_id="job-c",
            url="https://example.com/c",
            title="Customer Support",
            company="Gamma",
            location="Remote",
            description="Phone support.",
            tags=[],
        ),
    ]

    async def fake_remoteok(*, query=None, limit=50):
        return [sample[0], sample[2]]

    async def fake_remotive(*, query=None, limit=50):
        return [sample[1]]

    with (
        patch("app.agents.search_agent.get_sources") as gs,
        patch("app.services.job_ingest.score_job", new_callable=AsyncMock) as score,
    ):
        gs.return_value = {"remoteok": fake_remoteok, "remotive": fake_remotive}
        from app.agents.match_agent import MatchResult

        async def fake_score(job, user, **kwargs):
            scores = {"job-a": 90.0, "job-b": 80.0, "job-c": 30.0}
            val = scores.get(job.external_id, 50.0)
            return MatchResult(score=val, reason=f"Score {val}")

        score.side_effect = fake_score
        yield


def _reset_application_statuses() -> None:
    with SessionLocal() as session:
        for app in session.execute(select(Application)).scalars():
            app.status = ApplicationStatus.matched
        session.commit()


def _setup_profile(client: TestClient) -> None:
    client.put(
        "/api/profile",
        json={
            "name": "Lin",
            "target_roles": ["Backend engineer"],
            "skills": ["python"],
            "approval_top_n": 2,
        },
    )


def test_queue_top_n_promotes_highest_scores(seeded_jobs) -> None:
    with TestClient(app) as client:
        _reset_application_statuses()
        _setup_profile(client)
        client.post("/api/jobs/search", json={"score": True, "queue_for_approval": False})

        resp = client.post("/api/approvals/queue", json={"top_n": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["queued"] == 2
    assert len(body["items"]) == 2
    titles = [item["job"]["title"] for item in body["items"]]
    assert "Backend Engineer" in titles
    assert "Platform Engineer" in titles


def test_search_auto_queues_top_n(seeded_jobs) -> None:
    with TestClient(app) as client:
        _reset_application_statuses()
        _setup_profile(client)
        resp = client.post("/api/jobs/search", json={"score": True, "queue_for_approval": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["approval_queued"] == 2

    pending = client.get("/api/approvals")
    assert pending.status_code == 200
    assert pending.json()["summary"]["awaiting_approval"] == 2


def test_approve_and_reject(seeded_jobs) -> None:
    with TestClient(app) as client:
        _reset_application_statuses()
        _setup_profile(client)
        client.post("/api/approvals/queue", json={"top_n": 2})
        pending = client.get("/api/approvals").json()["items"]
        assert len(pending) == 2

        app_id = pending[0]["id"]
        approved = client.post(f"/api/approvals/{app_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"

        reject_id = pending[1]["id"]
        rejected = client.post(f"/api/approvals/{reject_id}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected_by_user"

        summary = client.get("/api/approvals").json()["summary"]
        assert summary["awaiting_approval"] == 0
        assert summary["approved"] == 1
        assert summary["rejected"] == 1


def test_cannot_approve_twice(seeded_jobs) -> None:
    with TestClient(app) as client:
        _reset_application_statuses()
        _setup_profile(client)
        client.post("/api/approvals/queue", json={"top_n": 1})
        app_id = client.get("/api/approvals").json()["items"][0]["id"]
        client.post(f"/api/approvals/{app_id}/approve")
        again = client.post(f"/api/approvals/{app_id}/approve")
    assert again.status_code == 409
