"""Link → ingest → filters → screen → tailor (+fact-check) → approval card → Apply/Skip."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.llm.base import ChatResponse, LLMError
from app.main import app
from app.messaging.service import MessagingService
from app.models import Application, ApplicationStatus, Job, MessagingLink, User
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline.flow import JobFlow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.prefs import Preferences, load_preferences
from app.pipeline.screen import hard_filter_reason, screen_job
from app.pipeline.tailor import cv_to_text, tailor_cv

TAILORED = {
    "resume": {
        "name": "Lin Ho",
        "headline": "Backend Engineer",
        "contact": ["lin@example.com"],
        "summary": "Backend engineer focused on Python APIs.",
        "skills": ["Python", "FastAPI", "Kubernetes"],
        "experience": [
            {
                "title": "Engineer",
                "organization": "Acme",
                "start": "2021",
                "end": "Present",
                "bullets": ["Built FastAPI services", "Led a team of 40 engineers"],
            }
        ],
        "education": [],
    },
    "cover_letter": "Dear Beta team,\nI'd love to help.\nLin",
    "changes": ["Led with API work", "Moved FastAPI to the top of skills"],
}


class ScriptedProvider:
    name = "scripted"
    default_model = "scripted"

    def __init__(self, screen: dict | None = None, unsupported: list[str] | None = None):
        self.screen = screen or {
            "decision": "keep",
            "score": 82,
            "summary": "Strong Python API overlap.",
            "reasons": ["Python", "APIs"],
            "gaps": ["No Go experience"],
        }
        self.unsupported = (
            unsupported if unsupported is not None else ["experience.0.b1", "skill.2"]
        )
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        system = messages[0].content
        self.prompts.append(messages[-1].content)
        if "screen job postings" in system:
            self.calls.append("screen")
            body = self.screen
        elif "tailor a candidate" in system:
            self.calls.append("tailor")
            body = TAILORED
        else:
            self.calls.append("fact_check")
            body = {"unsupported": self.unsupported}
        return ChatResponse(content=json.dumps(body), model="m", provider="scripted", raw={})


def _user() -> User:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.work_modes = ["remote", "hybrid"]
        user.target_locations = ["Toronto"]
        user.location = "Toronto, ON"
        user.preferences = {}
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user


def _job(**overrides) -> Job:
    data = dict(
        source="test",
        external_id=str(time.time_ns()),
        url="https://x.test/job",
        title="Backend Engineer",
        company="Beta",
        location="Toronto, ON",
        description="Python, FastAPI, Postgres.",
        work_mode="hybrid",
    )
    data.update(overrides)
    return Job(**data)


# --- filters + screening --------------------------------------------------------------


def test_hard_filters() -> None:
    user = _user()
    prefs = Preferences(excluded_companies=["Beta"], max_job_age_days=30)
    assert "skip Beta" in hard_filter_reason(user, _job(), prefs, user_requested=True)
    prefs = Preferences()
    assert hard_filter_reason(user, _job(), prefs, user_requested=False) is None
    old = _job(posted_at=datetime.now(timezone.utc) - timedelta(days=90))
    assert "Posted" in hard_filter_reason(user, old, prefs, user_requested=False)
    assert hard_filter_reason(user, old, prefs, user_requested=True) is None
    onsite = _job(work_mode="onsite")
    assert "Onsite" in hard_filter_reason(user, onsite, prefs, user_requested=False)


@pytest.mark.asyncio
async def test_screen_job_validates_model_output() -> None:
    user = _user()
    result = await screen_job(ScriptedProvider(), user, _job(), "Lin Ho, Python")
    assert (result.decision, result.score) == ("keep", 82)
    with pytest.raises(LLMError):
        await screen_job(ScriptedProvider(screen={"decision": "maybe"}), user, _job(), "x")


@pytest.mark.asyncio
async def test_tailor_removes_unverified_claims() -> None:
    user = _user()
    cv = await tailor_cv(ScriptedProvider(), user, _job(), "Lin Ho. Engineer at Acme. Python.")
    assert cv.resume.experience[0].bullets == ["Built FastAPI services"]
    assert cv.resume.skills == ["Python", "FastAPI"]
    assert set(cv.removed_claims) == {"Led a team of 40 engineers", "Kubernetes"}
    text = cv_to_text(cv.resume)
    assert "Built FastAPI services" in text and "40 engineers" not in text


@pytest.mark.asyncio
async def test_tailor_asks_for_the_base_resume_length() -> None:
    provider = ScriptedProvider()
    await tailor_cv(provider, _user(), _job(), "Lin Ho. Engineer at Acme. Python.", 2)
    assert "Length: at most 6 words, so it fits on 2 pages" in provider.prompts[0]
    await tailor_cv(provider, _user(), _job(), "Lin Ho. Python.")
    assert "fits on one page" in provider.prompts[2]


# --- ingest dedupe -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_dedupes_by_canonical_url(monkeypatch) -> None:
    url = f"https://job-boards.greenhouse.io/beta/jobs/{time.time_ns()}"

    async def fake_fetch(u, client=None):
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="greenhouse",
            title=f"Platform Engineer {time.time_ns()}",
            company="Beta",
            description="Go and Kubernetes.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    user = _user()
    first = await ingest_module.ingest_url(user.id, url)
    second = await ingest_module.ingest_url(user.id, url + "?utm_source=linkedin")
    assert not first.duplicate and second.duplicate
    assert first.application_id == second.application_id

    with pytest.raises(ingest_module.IngestError):
        await ingest_module.ingest_url(user.id, "http://localhost:8000/x")


# --- full flow through chat ----------------------------------------------------------------


class FakeChannel:
    name = "telegram"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, chat_id, text, buttons=None, *, html=False):
        self.sent.append({"text": text, "buttons": buttons})
        return str(len(self.sent))

    async def send_document(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append({"text": caption, "document": Path(path), "buttons": buttons})
        return str(len(self.sent))

    async def send_photo(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append({"text": caption, "photo": Path(path), "buttons": buttons})
        return str(len(self.sent))

    async def clear_buttons(self, chat_id, message_id):
        pass

    async def answer_callback(self, callback_id, text=None):
        pass

    async def aclose(self):
        pass


@pytest.fixture
def chat(tmp_path, monkeypatch):
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
    user = _user()
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.add(MessagingLink(user_id=user.id, channel="telegram", chat_id="9", from_id="9"))
        session.commit()

    provider = ScriptedProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")

    async def fake_pdf(build, out_path, max_pages=1):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 " + build(0)[:40].encode())
        return 1

    monkeypatch.setattr(flow_module, "html_to_pdf", fake_pdf)

    async def fake_fetch(u, client=None):
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title="Backend Engineer",
            company=f"Beta{time.time_ns()}",
            location="Remote",
            work_mode="remote",
            salary_text="CAD 140k–160k",
            description="Python, FastAPI.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    enqueued: list[int] = []
    from app.apply.runs import run_manager

    monkeypatch.setattr(run_manager, "enqueue", lambda app_id, **kw: enqueued.append(app_id) or 1)

    service = MessagingService()
    service.channel = FakeChannel()
    flow = JobFlow(service)
    flow.register()
    yield SimpleNamespace(
        service=service, flow=flow, user=user, enqueued=enqueued, provider=provider
    )
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()


def _tap(data: str) -> dict:
    return {
        "callback_query": {
            "id": "cb",
            "from": {"id": 9},
            "data": data,
            "message": {"message_id": 3, "chat": {"id": 9}},
        }
    }


@pytest.mark.asyncio
async def test_link_to_card_then_apply(chat) -> None:
    await chat.flow.handle_link(chat.user.id, f"https://jobs.lever.co/beta/{time.time_ns()}")
    card = next(m for m in chat.service.channel.sent if "document" in m)
    assert card["document"].name == "Lin_Ho_Resume.pdf"
    assert "Match 82/100" in card["text"] and "CAD 140k" in card["text"]
    assert "Removed 2 claim(s)" in card["text"]
    assert chat.provider.calls == ["screen", "tailor", "fact_check"]

    apply_button = card["buttons"][0][0]
    skip_button = card["buttons"][0][1]
    await chat.service.router.handle_update(_tap(apply_button.data))
    assert len(chat.enqueued) == 1
    with SessionLocal() as session:
        app_row = session.get(Application, chat.enqueued[0])
        assert app_row.status == ApplicationStatus.approved
        assert app_row.resume_doc_id is not None
    # Skip on the same card is now spent.
    await chat.service.router.handle_update(_tap(skip_button.data))
    with SessionLocal() as session:
        assert session.get(Application, chat.enqueued[0]).status == ApplicationStatus.approved


@pytest.mark.asyncio
async def test_skip_with_company_reason_excludes_company(chat) -> None:
    await chat.flow.handle_link(chat.user.id, f"https://jobs.lever.co/beta/{time.time_ns()}")
    card = next(m for m in reversed(chat.service.channel.sent) if "document" in m)
    await chat.service.router.handle_update(_tap(card["buttons"][0][1].data))
    reasons = chat.service.channel.sent[-1]["buttons"]
    company_button = next(b for row in reasons for b in row if b.text == "This company")
    await chat.service.router.handle_update(_tap(company_button.data))
    assert "won't send" in chat.service.channel.sent[-1]["text"]
    with SessionLocal() as session:
        user = session.get(User, chat.user.id)
        company = session.query(Job).order_by(Job.id.desc()).first().company
        assert company in load_preferences(user).excluded_companies


@pytest.mark.asyncio
async def test_bad_link_is_explained(chat) -> None:
    await chat.flow.handle_link(chat.user.id, "http://192.168.1.4/admin")
    assert "doesn't look like" in chat.service.channel.sent[-1]["text"]
