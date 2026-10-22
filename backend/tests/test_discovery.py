"""Job search: sources → dedupe → ingest → screen → best N cards; budgets; the daily schedule."""

from __future__ import annotations

import io
import time
from datetime import date, datetime
from datetime import time as dtime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_pipeline import ScriptedProvider

from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.llm.usage import BudgetExceededError
from app.main import app
from app.models import Application, ApplicationStatus, DiscoveryRun, Job, MessagingLink
from app.pipeline import discovery as discovery_module
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline import scheduler as scheduler_module
from app.pipeline.discovery import Discovery
from app.pipeline.flow import JobFlow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.prefs import Preferences, save_preferences
from app.pipeline.scheduler import Scheduler, is_due, next_search_at
from app.pipeline.sources.base import Candidate
from app.pipeline.state import set_paused


class ScoredProvider(ScriptedProvider):
    """Screens by title: the number in the title is the score."""

    async def chat(self, messages, **kwargs):
        system = messages[0].content
        if "screen job postings" in system:
            import json
            import re

            from app.llm.base import ChatResponse

            title = re.search(r"Title: (.*)", messages[1].content).group(1)
            score = int(re.search(r"(\d+)", title).group(1))
            self.calls.append(f"screen:{score}")
            body = {
                "decision": "keep" if score >= 60 else "drop",
                "score": score,
                "summary": "ok",
                "reasons": [],
                "gaps": [],
            }
            return ChatResponse(content=json.dumps(body), model="m", provider="s", raw={})
        return await super().chat(messages, **kwargs)


@pytest.fixture
def search(tmp_path, monkeypatch):
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        user = get_or_create_local_user(session)
        user.work_modes = ["remote"]
        user.target_roles = ["Backend Engineer"]
        save_preferences(
            user,
            Preferences(
                jobs_per_day=2, min_match_score=60, max_screens_per_run=10, web_search_enabled=False
            ),
        )
        # Start clean: nothing prepared today, nothing left over to screen or send.
        for app_row in session.execute(
            select(Application).where(Application.user_id == user.id)
        ).scalars():
            app_row.tailoring = None
            if app_row.status == ApplicationStatus.matched:
                app_row.status = ApplicationStatus.filtered_out
        session.commit()
        user_id = user.id
    set_paused(False)

    provider = ScoredProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")

    async def fake_pdf(build, out_path, max_pages=1):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF")
        return 1

    monkeypatch.setattr(flow_module, "html_to_pdf", fake_pdf)
    stamp = time.time_ns()
    titles = {
        f"https://jobs.lever.co/acme/{stamp}-{s}": f"Backend Engineer {s}" for s in (90, 75, 65, 40)
    }

    async def fake_fetch(u, client=None):
        # Live, readable postings (cards left by other tests are rechecked too).
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title=titles.get(u, "Earlier job"),
            # Unique per run: ingest treats the same company + title as a duplicate.
            company=f"Co{u.rsplit('/', 1)[-1]}",
            work_mode="remote",
            description="Python and FastAPI services. " * 20,
        )

    monkeypatch.setattr(discovery_module, "fetch_job_details", fake_fetch)
    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)

    async def fake_gather(self, user, prefs, summary):
        found = [Candidate(url=u, source="watchlist", title=t) for u, t in titles.items()]
        summary.sources["watchlist"] = len(found)
        return self._dedupe(found, prefs)

    monkeypatch.setattr(Discovery, "gather", fake_gather)
    flow = JobFlow(flow_module.job_flow.messaging)
    return discovery_module.Discovery(flow), user_id, provider, titles


@pytest.mark.asyncio
async def test_search_screens_all_and_sends_the_best_two(search) -> None:
    finder, user_id, provider, titles = search
    run_id = await finder.run(user_id, "manual")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, run_id)
        assert (row.status, row.found, row.screened, row.kept, row.sent) == ("done", 4, 4, 3, 2)
        cards = (
            session.execute(
                select(Application)
                .join(Job, Job.id == Application.job_id)
                .where(
                    Application.user_id == user_id,
                    Application.status == ApplicationStatus.awaiting_approval,
                    Job.title.in_(titles.values()),
                )
            )
            .scalars()
            .all()
        )
        assert sorted(c.screen["score"] for c in cards) == [75, 90]
    assert sorted(c for c in provider.calls if c.startswith("screen:")) == [
        "screen:40",
        "screen:65",
        "screen:75",
        "screen:90",
    ]

    # Quota used up: a second search finds nothing new and sends nothing.
    again = await finder.run(user_id, "manual")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, again)
        assert (row.found, row.sent) == (0, 0)

    # "Prepare more" sends the shortlisted 65.
    more = await finder.more(user_id, 3)
    with SessionLocal() as session:
        assert session.get(DiscoveryRun, more).sent == 1


@pytest.mark.asyncio
async def test_budget_stop_is_recorded(search, monkeypatch) -> None:
    finder, user_id, _, _ = search

    async def broke(app_id, *, user_requested=False):
        raise BudgetExceededError("Today's Claude budget ($10.00) is used up.")

    monkeypatch.setattr(finder.flow, "screen", broke)
    run_id = await finder.run(user_id, "schedule")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, run_id)
    assert row.status == "stopped" and "budget" in row.error


def test_schedule_due_and_catch_up() -> None:
    at = dtime(9, 0)
    morning = datetime(2026, 10, 19, 8, 0).astimezone()
    late = datetime(2026, 10, 19, 15, 0).astimezone()
    assert not is_due(morning, at, None)
    assert is_due(late, at, date(2026, 10, 18))  # PC was asleep at 9:00 → catch up
    assert not is_due(late, at, date(2026, 10, 19))
    assert next_search_at(morning, at, None).hour == 9
    assert next_search_at(late, at, date(2026, 10, 19)).day == 20


def test_scheduler_tick_respects_pause_and_runs_once(monkeypatch) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.onboarded_at = datetime(2026, 1, 1)
        save_preferences(user, Preferences(discovery_time="09:00"))
        session.commit()
    monkeypatch.setattr(scheduler_module, "last_search_day", lambda: None)
    started: list[str] = []

    class FakeSearch:
        running = False

        def start(self, user_id, trigger):
            started.append(trigger)
            return True

    sched = Scheduler(FakeSearch())
    now = datetime(2026, 10, 19, 10, 0).astimezone()
    set_paused(True)
    assert sched.tick(now) is False
    set_paused(False)
    assert sched.tick(now) is True and started == ["schedule"]
    assert scheduler_module.get_flag(scheduler_module.LAST_SEARCH_KEY) == "2026-10-19"


@pytest.mark.asyncio
async def test_only_live_readable_postings_become_jobs(search, monkeypatch) -> None:
    """Search results whose page can't be read are dropped; alert emails keep their snippet."""

    from app.pipeline.jd import JobFetchError, PostingClosedError

    finder, user_id, _, _ = search
    stamp = time.time_ns()
    pages = {
        f"https://jobs.lever.co/live/{stamp}": "Python " * 80,
        f"https://jobs.lever.co/closed/{stamp}": PostingClosedError(),
        f"https://jobs.lever.co/unreadable/{stamp}": JobFetchError("timeout"),
        f"https://jobs.lever.co/thin/{stamp}": "Apply now",
        f"https://www.linkedin.com/jobs/view/{stamp}": JobFetchError("login"),
    }

    async def fake_fetch(u, client=None):
        page = pages[u]
        if isinstance(page, Exception):
            raise page
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title="Backend Engineer 80",
            company=f"Co{stamp}{u[-20:]}",
            description=page,
        )

    monkeypatch.setattr(discovery_module, "fetch_job_details", fake_fetch)
    candidates = [
        Candidate(url=u, source="web_search", title="Backend Engineer 80", company="Made Up Inc")
        for u in pages
        if "linkedin" not in u
    ] + [
        Candidate(
            url=f"https://www.linkedin.com/jobs/view/{stamp}",
            source="email",
            title="Backend Engineer 80",
            company=f"Alerted{stamp}",
            snippet="From a LinkedIn alert",
        )
    ]
    from app.pipeline.discovery import SearchSummary

    with SessionLocal() as session:
        user = session.get(__import__("app.models", fromlist=["User"]).User, user_id)
        session.expunge(user)
    app_ids = await finder.ingest(user, candidates, SearchSummary())
    with SessionLocal() as session:
        urls = sorted(session.get(Job, session.get(Application, i).job_id).url for i in app_ids)
    assert urls == sorted(
        [f"https://jobs.lever.co/live/{stamp}", f"https://www.linkedin.com/jobs/view/{stamp}"]
    )


@pytest.mark.asyncio
async def test_recheck_withdraws_cards_whose_posting_closed(search, monkeypatch) -> None:
    from app.pipeline.discovery import recheck_open_postings
    from app.pipeline.jd import PostingClosedError

    finder, user_id, _, titles = search
    await finder.run(user_id, "manual")
    with SessionLocal() as session:
        cards = session.execute(
            select(Application.id, Job.url)
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.user_id == user_id,
                Application.status == ApplicationStatus.awaiting_approval,
                Job.title.in_(titles.values()),
            )
        ).all()
    closed_url = cards[0][1]

    async def fetch(u, client=None):
        if u == closed_url:
            raise PostingClosedError()
        return JobDetails(url=u, canonical_url=canonicalize_url(u), ats="lever", title="x")

    monkeypatch.setattr(discovery_module, "fetch_job_details", fetch)
    gone = await recheck_open_postings(user_id)
    assert cards[0][0] in gone
    with SessionLocal() as session:
        app = session.get(Application, cards[0][0])
        assert app.status == ApplicationStatus.filtered_out
        assert app.stage_reason == "The posting was taken down."
