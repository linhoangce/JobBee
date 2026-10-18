"""Job search agent tests (mocked external APIs)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agents.sources.base import RawJobListing
from app.main import app


@pytest.fixture
def mock_sources():
    sample = [
        RawJobListing(
            source="remoteok",
            external_id="acme-backend",
            url="https://remoteok.com/remote-jobs/acme-backend",
            title="Backend Engineer",
            company="Acme",
            location="Remote",
            description="Python, FastAPI, Postgres.",
            tags=["python", "backend"],
        ),
        RawJobListing(
            source="remotive",
            external_id="42",
            url="https://remotive.com/remote-jobs/42",
            title="Platform Engineer",
            company="Beta",
            location="Remote",
            description="Kubernetes, Go.",
            tags=["devops"],
        ),
    ]

    async def fake_remoteok(*, query=None, limit=50):
        return [sample[0]]

    async def fake_remotive(*, query=None, limit=50):
        return [sample[1]]

    with (
        patch("app.agents.search_agent.get_sources") as gs,
        patch("app.services.job_ingest.score_job", new_callable=AsyncMock) as score,
    ):
        gs.return_value = {"remoteok": fake_remoteok, "remotive": fake_remotive}

        from app.agents.match_agent import MatchResult

        score.return_value = MatchResult(score=82.0, reason="Strong Python overlap.")
        yield score


def test_list_sources() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/jobs/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert "email_linkedin" in body["available"]
    assert "remoteok" not in body["available"]
    assert "remotive" not in body["available"]
    assert "linkedin" in body["blocked"]


def test_search_without_board_sources_returns_warning() -> None:
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend engineer"], "skills": ["python"]},
        )
        resp = client.post("/api/jobs/search", json={"score": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jobs_found"] == 0
    assert any("inbox" in w.lower() or "gmail" in w.lower() for w in body["warnings"])


def test_retired_board_source_rejected() -> None:
    from app.agents.sources.base import JobSourceError
    from app.agents.sources.registry import get_sources

    with pytest.raises(JobSourceError, match="remoteok"):
        get_sources(["remoteok"])


def test_search_requires_profile_fields() -> None:
    with TestClient(app) as client:
        client.put("/api/profile", json={"target_roles": [], "skills": []})
        resp = client.post("/api/jobs/search", json={})
    assert resp.status_code == 400


def test_search_upserts_and_scores(mock_sources) -> None:
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={
                "name": "Lin",
                "target_roles": ["Backend engineer"],
                "skills": ["python"],
            },
        )
        resp = client.post("/api/jobs/search", json={"score": True, "queue_for_approval": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["jobs_upserted"] == 2
    assert body["jobs_scored"] == 2
    mock_sources.assert_called()


def test_search_rescores_existing_jobs(mock_sources) -> None:
    """Second search with no new listings should re-rank via heuristics (no LLM)."""
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={
                "name": "Lin",
                "target_roles": ["Backend engineer"],
                "skills": ["python"],
                "location": "Toronto, ON",
                "work_modes": ["remote"],
            },
        )
        client.post("/api/jobs/search", json={"score": True})
        mock_sources.reset_mock()

        second = client.post("/api/jobs/search", json={"score": True})
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["jobs_upserted"] == 0
    assert body["jobs_scored"] == 2
    assert all(j["match_score"] is not None for j in body["jobs"])
    mock_sources.assert_not_called()


def test_linkedin_source_rejected() -> None:
    from app.agents.sources.registry import get_sources
    from app.agents.sources.base import JobSourceError

    with pytest.raises(JobSourceError, match="linkedin"):
        get_sources(["linkedin"])
