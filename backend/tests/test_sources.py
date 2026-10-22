"""Job sources: public ATS boards (mocked HTTP), title relevance, and web-search parsing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from app.llm.base import LLMError
from app.models import User
from app.pipeline.sources import ats_boards, web_search
from app.pipeline.sources.ats_boards import board_from_url, find_board, list_board, role_relevance


def _client(routes: dict[str, object]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={"error": "not found"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_board_from_url() -> None:
    assert board_from_url("https://job-boards.greenhouse.io/stripe/jobs/123") == (
        "greenhouse",
        "stripe",
    )
    assert board_from_url("boards.greenhouse.io/airbnb") == ("greenhouse", "airbnb")
    assert board_from_url("https://jobs.lever.co/netflix/abc-123") == ("lever", "netflix")
    assert board_from_url("https://jobs.ashbyhq.com/openai") == ("ashby", "openai")
    assert board_from_url("https://example.com/careers") is None


@pytest.mark.asyncio
async def test_find_board_by_name_probes_each_ats() -> None:
    client = _client({"https://api.ashbyhq.com/posting-api/job-board/acmerobotics": {"jobs": []}})
    board = await find_board("Acme Robotics", client=client)
    assert (board.ats, board.slug, board.name) == ("ashby", "acmerobotics", "Acme Robotics")
    assert await find_board("Nobody Corp", client=client) is None


@pytest.mark.asyncio
async def test_list_boards() -> None:
    client = _client(
        {
            "https://boards-api.greenhouse.io/v1/boards/acme/jobs": {
                "jobs": [
                    {
                        "title": "Backend Engineer",
                        "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/1",
                        "location": {"name": "Toronto"},
                        "first_published": "2026-10-10T12:00:00Z",
                    }
                ]
            },
            "https://api.lever.co/v0/postings/beta": [
                {
                    "text": "Platform Engineer",
                    "hostedUrl": "https://jobs.lever.co/beta/2",
                    "categories": {"location": "Remote"},
                    "createdAt": 1760000000000,
                }
            ],
            "https://api.ashbyhq.com/posting-api/job-board/gamma": {
                "jobs": [
                    {
                        "title": "SRE",
                        "jobUrl": "https://jobs.ashbyhq.com/gamma/3",
                        "location": "NYC",
                    },
                    {
                        "title": "Hidden",
                        "jobUrl": "https://jobs.ashbyhq.com/gamma/4",
                        "isListed": False,
                    },
                ]
            },
        }
    )
    gh = await list_board("greenhouse", "acme", "Acme", client=client)
    assert gh[0].url.endswith("/jobs/1") and gh[0].location == "Toronto" and gh[0].posted_at
    lv = await list_board("lever", "beta", "Beta", client=client)
    assert lv[0].title == "Platform Engineer" and lv[0].source == "watchlist"
    ab = await list_board("ashby", "gamma", "Gamma", client=client)
    assert [c.title for c in ab] == ["SRE"]
    with pytest.raises(ats_boards.SourceError):
        await list_board("greenhouse", "missing", "Missing", client=client)


def test_role_relevance() -> None:
    roles = ["Backend Engineer", "Platform Engineer"]
    assert role_relevance("Senior Backend Engineer, Payments", roles) > role_relevance(
        "Software Engineer", roles
    )
    assert role_relevance("Software Engineer", roles) >= 1
    assert role_relevance("Account Executive", roles) == 0
    assert role_relevance("Anything", []) == 1


def test_parse_postings_keeps_only_allowed_https_postings() -> None:
    text = json.dumps(
        {
            "postings": [
                {"url": "https://jobs.lever.co/acme/1", "title": "Backend", "company": "Acme"},
                {"url": "https://jobs.lever.co/acme/1", "title": "dupe"},
                {"url": "http://jobs.lever.co/acme/2", "title": "not https"},
                {"url": "https://evil.example.com/jobs/3", "title": "off-list"},
                {"url": "https://acme.wd5.myworkdayjobs.com/x/job/4", "title": "Workday"},
            ]
        }
    )
    found = web_search.parse_postings("Here you go:\n" + text, web_search.ATS_DOMAINS)
    assert [c.url for c in found] == [
        "https://jobs.lever.co/acme/1",
        "https://acme.wd5.myworkdayjobs.com/x/job/4",
    ]
    with pytest.raises(LLMError):
        web_search.parse_postings('{"postings": "nope"}', web_search.ATS_DOMAINS)


@pytest.mark.asyncio
async def test_search_postings_restricts_domains_and_continues_paused_turns(monkeypatch) -> None:
    from sqlalchemy import delete

    from app.db import SessionLocal
    from app.models import LlmUsage

    with SessionLocal() as session:
        session.execute(delete(LlmUsage))
        session.commit()
    calls: list[dict] = []
    usage = SimpleNamespace(
        input_tokens=1000,
        output_tokens=200,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,
        server_tool_use=SimpleNamespace(web_search_requests=4),
    )
    answer = '{"postings": [{"url": "https://jobs.ashbyhq.com/acme/9", "title": "Backend"}]}'

    class Messages:
        async def create(self, **params):
            calls.append(params)
            if len(calls) == 1:
                return SimpleNamespace(stop_reason="pause_turn", content=[], usage=usage)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=answer)],
                usage=usage,
            )

    user = User(
        name="Lin",
        target_roles=["Backend Engineer"],
        target_locations=["Toronto"],
        work_modes=["remote"],
        years_experience=6,
    )
    found = await web_search.search_postings(
        SimpleNamespace(messages=Messages()),
        user,
        excluded=["Initech"],
        max_age_days=14,
        extra_domains=["careers.acme.com"],
    )
    assert [c.url for c in found] == ["https://jobs.ashbyhq.com/acme/9"]
    tool = calls[0]["tools"][0]
    assert tool["type"] == "web_search_20260209"
    assert "careers.acme.com" in tool["allowed_domains"] and tool["max_uses"] == 10
    assert "Initech" in calls[0]["messages"][0]["content"]
    assert len(calls) == 2 and calls[1]["messages"][-1]["role"] == "assistant"
    with SessionLocal() as session:
        rows = session.query(LlmUsage).all()
    assert len(rows) == 2 and rows[0].task == "discover" and rows[0].web_searches == 4


# --- posting verification (pipeline/jd.py) ---------------------------------------------


@pytest.mark.asyncio
async def test_workday_postings_come_from_the_json_api_and_closed_ones_are_refused() -> None:
    from app.pipeline.jd import PostingClosedError, fetch_job_details, workday_api_url

    live = "https://acme.wd1.myworkdayjobs.com/en-US/Ext/job/Toronto-ON-CAN/QA-Engineer_R1"
    gone = "https://acme.wd1.myworkdayjobs.com/de-DE/Ext/job/Toronto-ON-CAN/Old-Role_R2"
    assert workday_api_url(live) == (
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/Ext/job/Toronto-ON-CAN/QA-Engineer_R1"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("QA-Engineer_R1"):
            return httpx.Response(
                200,
                json={
                    "jobPostingInfo": {
                        "title": "QA Engineer",
                        "jobDescription": "<p>Own the test strategy.</p><p>Hybrid in Toronto.</p>",
                        "location": "Toronto, ON, CAN",
                        "startDate": "2026-10-02",
                        "canApply": True,
                        "posted": True,
                    },
                    "hiringOrganization": {"name": "Acme Corp"},
                },
            )
        return httpx.Response(403, json={"errorCode": "S22", "message": "permission denied"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    details = await fetch_job_details(live, client=client)
    assert (details.title, details.company) == ("QA Engineer", "Acme Corp")
    assert "test strategy" in details.description and details.posted_at is not None
    with pytest.raises(PostingClosedError):
        await fetch_job_details(gone, client=client)


@pytest.mark.asyncio
async def test_closed_pages_and_404s_are_recognised() -> None:
    from app.pipeline.jd import PostingClosedError, fetch_job_details

    def handler(request: httpx.Request) -> httpx.Response:
        if "boards-api.greenhouse.io" in str(request.url):
            return httpx.Response(404, json={"status": 404})
        if request.url.path == "/careers/closed":
            return httpx.Response(
                200,
                text="<html><body><h1>Sorry, this job is no longer available.</h1></body></html>",
            )
        return httpx.Response(
            200,
            text="<html><body><h1>Backend Engineer</h1>"
            "<p>Build APIs. Python, Postgres.</p></body></html>",
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(PostingClosedError):
        await fetch_job_details("https://job-boards.greenhouse.io/acme/jobs/1", client=client)
    with pytest.raises(PostingClosedError):
        await fetch_job_details("https://careers.acme.com/careers/closed", client=client)
    page = await fetch_job_details("https://careers.acme.com/careers/open", client=client)
    assert "Build APIs" in page.description
