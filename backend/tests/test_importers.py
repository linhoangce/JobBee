"""Unit tests for the importer pipeline.

These cover the per-importer logic; the `/api/profile/import` HTTP route is
exercised in `test_profile_import.py`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.importers import import_github, merge_drafts
from app.importers.base import Draft
from app.importers.resume import import_resume
from app.llm.base import ChatMessage, ChatResponse, LLMProvider


class _StubLLM(LLMProvider):
    """A fake LLMProvider that returns a fixed string for `chat()`."""

    name = "stub"
    default_model = "stub-1"

    def __init__(self, content: str) -> None:
        self.content = content
        self.last_messages: list[ChatMessage] | None = None
        self.last_thinking: bool | None = None

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
        self.last_messages = messages
        self.last_thinking = thinking
        return ChatResponse(
            content=self.content,
            model=model or self.default_model,
            provider=self.name,
            raw={},
        )


# --- merge_drafts -----------------------------------------------------------


def test_merge_scalar_first_non_empty_wins() -> None:
    higher = Draft(source="resume", data={"name": "Lin", "headline": "Engineer"})
    lower = Draft(source="github", data={"name": "Lin Hong", "location": "Toronto"})
    merged = merge_drafts([higher, lower])
    assert merged.data["name"] == "Lin"
    assert merged.sources["name"] == "resume"
    assert merged.data["headline"] == "Engineer"
    assert merged.data["location"] == "Toronto"
    assert merged.sources["location"] == "github"


def test_merge_lists_union_preserving_priority_order() -> None:
    higher = Draft(source="resume", data={"skills": ["python", "TypeScript"]})
    lower = Draft(source="github", data={"skills": ["typescript", "rust", "python"]})
    merged = merge_drafts([higher, lower])
    # Resume's "TypeScript" wins for ordering; "rust" is appended from github;
    # github's "typescript" / "python" are deduped case-insensitively.
    assert merged.data["skills"] == ["python", "TypeScript", "rust"]
    assert merged.sources["skills"] == "resume"


def test_merge_dict_links_merges_with_priority() -> None:
    higher = Draft(source="resume", data={"links": {"linkedin": "u1"}})
    lower = Draft(source="github", data={"links": {"github": "g1", "linkedin": "u2"}})
    merged = merge_drafts([higher, lower])
    assert merged.data["links"] == {"linkedin": "u1", "github": "g1"}


# --- import_github ----------------------------------------------------------


def _mock_transport(routes: dict[str, tuple[int, Any]]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        if key not in routes:
            return httpx.Response(404, json={"message": "no mock for " + key})
        status, body = routes[key]
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_import_github_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    user_body = {
        "login": "lin",
        "name": "Lin Hong",
        "bio": "Senior software engineer",
        "location": "Toronto, ON",
        "blog": "lin.dev",
        "html_url": "https://github.com/lin",
    }
    repos_body = [
        {
            "name": "platform",
            "description": "Internal platform tooling",
            "language": "Python",
            "topics": ["python", "fastapi", "kubernetes"],
            "stargazers_count": 42,
            "fork": False,
        },
        {
            "name": "ui",
            "description": "TS app",
            "language": "TypeScript",
            "topics": ["typescript", "react"],
            "stargazers_count": 10,
            "fork": False,
        },
        {
            "name": "fork-of-something",
            "description": "ignored",
            "language": "Go",
            "topics": [],
            "stargazers_count": 100,
            "fork": True,
        },
    ]
    transport = _mock_transport(
        {
            "GET /users/lin": (200, user_body),
            "GET /users/lin/repos": (200, repos_body),
        }
    )

    real_async_client = httpx.AsyncClient

    def _patched(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr("app.importers.github.httpx.AsyncClient", _patched)

    draft = await import_github("lin")
    assert draft.source == "github"
    assert draft.data["name"] == "Lin Hong"
    assert draft.data["headline"] == "Senior software engineer"
    assert draft.data["location"] == "Toronto, ON"
    assert draft.data["links"]["github"] == "https://github.com/lin"
    assert draft.data["links"]["portfolio"] == "https://lin.dev"

    skills = draft.data["skills"]
    assert "python" in skills
    assert "typescript" in skills
    assert "fastapi" in skills
    # Fork shouldn't contribute "go".
    assert "go" not in skills
    assert "golang" not in skills


@pytest.mark.asyncio
async def test_import_github_404(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = _mock_transport({})  # everything 404s
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "app.importers.github.httpx.AsyncClient",
        lambda *a, **kw: real(*a, transport=transport, **kw),
    )
    from app.importers.base import ImporterError

    with pytest.raises(ImporterError):
        await import_github("does-not-exist-xyz-42")


# --- import_resume ----------------------------------------------------------


@pytest.mark.asyncio
async def test_import_resume_parses_clean_json() -> None:
    payload = (
        '{"name":"Lin Hong","headline":"Senior software engineer",'
        '"location":"Toronto, ON","years_experience":7,'
        '"summary":"Builds platform tooling and llm-powered agents.",'
        '"target_roles":["Software engineer","Platform engineer"],'
        '"skills":["python","typescript","postgres"],'
        '"links":{"linkedin":"https://www.linkedin.com/in/lin/"}}'
    )
    stub = _StubLLM(payload)
    draft = await import_resume(
        "Lin Hong, senior engineer.", provider=stub, label="resume (lin.pdf)"
    )
    assert draft.source == "resume (lin.pdf)"
    assert draft.data["name"] == "Lin Hong"
    assert draft.data["years_experience"] == 7
    assert draft.data["target_roles"] == ["Software engineer", "Platform engineer"]
    assert draft.data["skills"] == ["python", "typescript", "postgres"]
    assert draft.data["links"]["linkedin"].startswith("https://")
    assert stub.last_thinking is False


@pytest.mark.asyncio
async def test_import_resume_strips_code_fences() -> None:
    fenced = '```json\n{"name":"Lin","skills":["python"]}\n```'
    stub = _StubLLM(fenced)
    draft = await import_resume("Resume text.", provider=stub)
    assert draft.data["name"] == "Lin"
    assert draft.data["skills"] == ["python"]


@pytest.mark.asyncio
async def test_import_resume_recovers_truncated_json() -> None:
    truncated = (
        '{\n  "name": "Linh Hoang",\n  "headline": "Software QA Engineer",\n'
        '  "email": "pelinhoang.ce@gmail.com",\n  "location": "",\n'
        '  "years_experience": 4,\n  "summary": "QA Engineer specializing in Playwright, '
    )
    stub = _StubLLM(truncated)
    draft = await import_resume("Resume text.", provider=stub)
    assert draft.data["name"] == "Linh Hoang"
    assert draft.data["email"] == "pelinhoang.ce@gmail.com"
    assert draft.data["years_experience"] == 4


@pytest.mark.asyncio
async def test_import_resume_recovers_json_inside_thought_tags() -> None:
    payload = (
        '<thought>{"name":"Linh Hoang","email":"lin@example.com","skills":["python"]}</thought>'
    )
    stub = _StubLLM(payload)
    draft = await import_resume("Resume text.", provider=stub)
    assert draft.data["name"] == "Linh Hoang"
    assert draft.data["skills"] == ["python"]


@pytest.mark.asyncio
async def test_import_resume_retries_when_truncated() -> None:
    truncated = '{"location": "Toronto'
    complete = (
        '{"name":"Lin","email":"lin@example.com","summary":"Short summary",'
        '"skills":["python"],"target_roles":["Engineer"]}'
    )

    class _RetryLLM(_StubLLM):
        def __init__(self) -> None:
            super().__init__(truncated)
            self.calls = 0

        async def chat(self, messages, **kwargs):  # type: ignore[override]
            self.calls += 1
            if self.calls == 1:
                return await super().chat(messages, **kwargs)
            self.content = complete
            return await super().chat(messages, **kwargs)

    stub = _RetryLLM()
    draft = await import_resume("Resume text.", provider=stub)
    assert stub.calls == 2
    assert draft.data["skills"] == ["python"]


@pytest.mark.asyncio
async def test_import_resume_rejects_garbage() -> None:
    stub = _StubLLM("sorry I can't help with that")
    from app.importers.base import ImporterError

    with pytest.raises(ImporterError):
        await import_resume("Resume text.", provider=stub)


@pytest.mark.asyncio
async def test_import_resume_recovers_partial_validation() -> None:
    # `years_experience` is bogus -> validator should drop it but keep the rest.
    payload = '{"name":"Lin","years_experience":"not-a-number","skills":["python"]}'
    stub = _StubLLM(payload)
    draft = await import_resume("Resume.", provider=stub)
    assert draft.data["name"] == "Lin"
    assert "years_experience" not in draft.data
    assert draft.data["skills"] == ["python"]
