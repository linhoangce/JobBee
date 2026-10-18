"""End-to-end test for POST /api/profile/import.

We stub the LLM provider so the resume importer is deterministic, and we
stub `httpx.AsyncClient` in the GitHub importer so it doesn't touch the
network. The actual route logic (file save, merge, response shape) is
exercised for real.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.llm.base import ChatMessage, ChatResponse, LLMProvider
from app.main import app


_RESUME_PAYLOAD = (
    '{"name":"Lin Hong",'
    '"headline":"Senior software engineer",'
    '"location":"Toronto, ON",'
    '"years_experience":7,'
    '"summary":"Backend + llm tooling.",'
    '"target_roles":["Software engineer","Platform engineer"],'
    '"skills":["python","typescript","postgres"],'
    '"links":{"linkedin":"https://www.linkedin.com/in/lin/"}}'
)


class _StubLLM(LLMProvider):
    name = "stub"
    default_model = "stub-1"

    async def chat(  # type: ignore[override]
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> ChatResponse:
        return ChatResponse(
            content=_RESUME_PAYLOAD,
            model=model or self.default_model,
            provider=self.name,
            raw={},
        )


@pytest.fixture
def stub_llm_and_github(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1) Stub the LLM provider singleton so resume extraction is deterministic.
    import app.api.profile as profile_router

    monkeypatch.setattr(profile_router, "get_llm_provider", lambda: _StubLLM())

    # 2) Stub httpx.AsyncClient inside the github importer.
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/users/lin"):
            return httpx.Response(
                200,
                json={
                    "login": "lin",
                    "name": "Lin Hong",
                    "bio": "Loves platform work",
                    "location": "Toronto",
                    "blog": "https://lin.dev",
                    "html_url": "https://github.com/lin",
                },
            )
        if path.endswith("/users/lin/repos"):
            return httpx.Response(
                200,
                json=[
                    {
                        "name": "core",
                        "description": "core platform",
                        "language": "Python",
                        "topics": ["python", "fastapi"],
                        "stargazers_count": 5,
                        "fork": False,
                    }
                ],
            )
        return httpx.Response(404, json={"message": "no mock"})

    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.importers.github.httpx.AsyncClient",
        lambda *a, **kw: real(*a, transport=transport, **kw),
    )


def test_import_requires_at_least_one_source(stub_llm_and_github: None) -> None:
    with TestClient(app) as client:
        resp = client.post("/api/profile/import", data={})
    # No file and no username -> 400.
    assert resp.status_code == 400


def test_import_resume_only(stub_llm_and_github: None) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/profile/import",
            files={"resume_file": ("resume.txt", b"Lin Hong, senior engineer.", "text/plain")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["name"] == "Lin Hong"
    assert body["draft"]["years_experience"] == 7
    assert body["draft"]["target_roles"] == ["Software engineer", "Platform engineer"]
    assert body["document_id"] is not None
    # Provenance pill should attribute everything to the resume.
    assert body["sources"]["name"].startswith("resume")
    assert body["sources"]["skills"].startswith("resume")
    assert body["warnings"] == []


def test_import_github_only(stub_llm_and_github: None) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/profile/import",
            data={"github_username": "lin"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["draft"]["name"] == "Lin Hong"
    assert body["draft"]["location"] == "Toronto"
    assert body["sources"]["name"] == "github"
    assert body["document_id"] is None


def test_import_resume_and_github_resume_wins_scalars(stub_llm_and_github: None) -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/profile/import",
            files={"resume_file": ("resume.txt", b"Lin Hong, senior engineer.", "text/plain")},
            data={"github_username": "lin"},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # Resume's "Toronto, ON" should beat GitHub's "Toronto".
    assert body["draft"]["location"] == "Toronto, ON"
    assert body["sources"]["location"].startswith("resume")
    # GitHub-only fields still come through.
    assert "github" in body["draft"]["links"]
    # Skills should be a union with case-insensitive dedupe.
    skills = body["draft"]["skills"]
    assert "python" in skills
    assert "typescript" in skills
    assert "fastapi" in skills
