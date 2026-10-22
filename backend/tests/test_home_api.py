"""The browser flow: setup, preferences, paste a link → match card → Apply / Skip, files, pause."""

from __future__ import annotations

import io
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from test_pipeline import ScriptedProvider

from app.api import documents as documents_api
from app.api import setup as setup_api
from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.main import app
from app.models import Application, ApplicationStatus, Document, DocumentKind, MessagingLink, User
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline.flow import job_flow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.state import is_paused, set_paused
from app.services.secrets import ANTHROPIC_API_KEY, get_secret_store


def _wait_for(client: TestClient, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get("/api/home").json()
        if predicate(body):
            return body
        time.sleep(0.03)
    raise AssertionError(f"home never matched: {body.get('needs_you')}")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """A user with a resume and a Claude key; Claude, PDF rendering and fetching are faked."""

    get_secret_store().set(ANTHROPIC_API_KEY, "sk-ant-test-key-000000000000")
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        user = get_or_create_local_user(session)
        user.work_modes = ["remote", "hybrid"]
        user.target_roles = ["Backend Engineer"]
        user.target_locations = ["Toronto"]
        user.preferences = {}
        session.commit()
    set_paused(False)

    provider = ScriptedProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(documents_api, "SERVE_ROOTS", [tmp_path])

    rendered: list[str] = []

    async def fake_pdf(build, out_path, max_pages=1):
        rendered.append(build(0))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 tailored")
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
            description="Python, FastAPI.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    enqueued: list[int] = []
    from app.apply.runs import run_manager

    monkeypatch.setattr(
        run_manager, "enqueue", lambda app_id, **kw: enqueued.append(app_id) or 4242
    )
    job_flow.notices.clear()
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
        client.enqueued = enqueued  # type: ignore[attr-defined]
        client.provider = provider  # type: ignore[attr-defined]
        client.rendered = rendered  # type: ignore[attr-defined]
        yield client


def _paste(client: TestClient) -> int:
    resp = client.post(
        "/api/jobs/link", json={"url": f"https://jobs.lever.co/beta/{time.time_ns()}"}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["processing"] is True
    return resp.json()["application_id"]


def _match(body: dict, app_id: int) -> dict | None:
    return next(
        (c for c in body["needs_you"] if c["type"] == "match" and c["application_id"] == app_id),
        None,
    )


def test_link_becomes_a_match_card_then_apply(ready) -> None:
    app_id = _paste(ready)
    body = _wait_for(ready, lambda b: _match(b, app_id) is not None)
    card = _match(body, app_id)
    assert card["score"] == 82 and card["reasons"] == ["Python", "APIs"]
    assert card["changes"] and "Kubernetes" in card["removed_claims"]
    assert body["agent"]["sent_today"] >= 1

    pdf = ready.get(f"/api/documents/{card['resume_doc_id']}/file")
    assert pdf.status_code == 200 and pdf.content == b"%PDF-1.4 tailored"
    assert pdf.headers["content-type"] == "application/pdf"

    applied = ready.post(f"/api/applications/{app_id}/apply")
    assert applied.status_code == 200 and applied.json()["run_id"] == 4242
    assert ready.enqueued == [app_id]
    # Double click / the Telegram button after the UI: already handled.
    assert ready.post(f"/api/applications/{app_id}/apply").status_code == 409
    assert ready.post(f"/api/applications/{app_id}/skip", json={}).status_code == 409
    assert _match(ready.get("/api/home").json(), app_id) is None


def test_skip_with_company_reason_learns(ready) -> None:
    app_id = _paste(ready)
    _wait_for(ready, lambda b: _match(b, app_id) is not None)
    resp = ready.post(f"/api/applications/{app_id}/skip", json={"reason": "company"})
    assert resp.status_code == 200 and "won't send" in resp.json()["message"]
    detail = ready.get(f"/api/applications/{app_id}").json()
    assert detail["group"] == "skipped" and detail["stage_reason"] == "Skipped: This company"
    prefs = ready.get("/api/preferences").json()
    assert detail["job"]["company"] in prefs["excluded_companies"]


def test_failed_preparation_shows_a_notice_and_retries(ready, monkeypatch) -> None:
    from app.llm.base import LLMError

    class Broken(ScriptedProvider):
        async def chat(self, messages, **kwargs):
            raise LLMError("Claude error 400: Your credit balance is too low.")

    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: Broken())
    app_id = _paste(ready)
    body = _wait_for(
        ready,
        lambda b: any(
            c["type"] == "notice" and c["application_id"] == app_id for c in b["needs_you"]
        ),
    )
    notice = next(c for c in body["needs_you"] if c["type"] == "notice")
    assert "credit balance" in notice["text"] and notice["retry"] is True

    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: ready.provider)
    assert ready.post(f"/api/agent/notices/{notice['id']}/retry").status_code == 200
    _wait_for(ready, lambda b: _match(b, app_id) is not None)


def test_cv_style_is_a_preference_and_a_card_can_change_it(ready) -> None:
    assert ready.get("/api/preferences").json()["document_style"] == "modern"
    assert ready.put("/api/preferences", json={"document_style": "fancy"}).status_code == 400
    saved = ready.put("/api/preferences", json={"document_style": "classic"}).json()
    assert saved["document_style"] == "classic"
    styles = {s["id"] for s in ready.get("/api/documents/styles").json()}
    assert {"modern", "classic", "minimal", "compact", "bold"} <= styles

    app_id = _paste(ready)
    card = _match(_wait_for(ready, lambda b: _match(b, app_id) is not None), app_id)
    assert card["style"] == "classic" and card["can_restyle"] is True
    resume_markup, letter_markup = ready.rendered[-2:]
    assert "Cambria" in resume_markup and "Re: Backend Engineer" in letter_markup

    claude_calls = len(ready.provider.calls)
    resp = ready.post(f"/api/applications/{app_id}/style", json={"style": "bold"})
    assert resp.status_code == 200 and "Bold" in resp.json()["message"]
    assert len(ready.provider.calls) == claude_calls  # restyling never calls Claude
    card = _match(ready.get("/api/home").json(), app_id)
    assert card["style"] == "bold" and card["changes"]
    assert "#1f2937" in ready.rendered[-2]
    assert ready.get(f"/api/documents/{card['resume_doc_id']}/file").status_code == 200
    assert ready.post(f"/api/applications/{app_id}/style", json={"style": "x"}).status_code == 400

    preview = ready.get("/api/documents/styles/minimal/preview?kind=cover_letter")
    assert preview.status_code == 200 and "text/html" in preview.headers["content-type"]
    assert "default-src 'none'" in preview.headers["content-security-policy"]
    assert "grid-template-columns" in preview.text and "Hiring Team" in preview.text
    assert ready.get("/api/documents/styles/nope/preview").status_code == 404

    ready.post(f"/api/applications/{app_id}/skip", json={})
    late = ready.post(f"/api/applications/{app_id}/style", json={"style": "modern"})
    assert late.status_code == 409


def test_card_from_a_replaced_resume_can_be_tailored_again(ready) -> None:
    app_id = _paste(ready)
    card = _match(_wait_for(ready, lambda b: _match(b, app_id) is not None), app_id)
    assert card["older_resume"] is False
    with SessionLocal() as session:
        row = session.get(Application, app_id)
        row.tailoring = {**row.tailoring, "prepared_at": "2026-01-01T00:00:00+00:00"}
        session.commit()
    ready.post(
        "/api/documents",
        data={"kind": "resume"},
        files={"file": ("cv2.txt", io.BytesIO(b"Lin Ho. Co-founder at Bee."), "text/plain")},
    )
    assert _match(ready.get("/api/home").json(), app_id)["older_resume"] is True

    claude_calls = len(ready.provider.calls)
    resp = ready.post(f"/api/applications/{app_id}/prepare")
    assert resp.status_code == 202 and "current resume" in resp.json()["message"]
    _wait_for(ready, lambda b: (_match(b, app_id) or {}).get("older_resume") is False)
    assert len(ready.provider.calls) > claude_calls


def test_bad_link_is_a_friendly_400(ready) -> None:
    resp = ready.post("/api/jobs/link", json={"url": "http://192.168.1.4/admin"})
    assert resp.status_code == 400
    assert "doesn't look like" in resp.json()["detail"]


def test_link_needs_claude_key(ready) -> None:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    resp = ready.post("/api/jobs/link", json={"url": "https://jobs.lever.co/beta/1"})
    assert resp.status_code == 400 and "Claude" in resp.json()["detail"]


def test_files_are_owned_and_confined(ready, tmp_path) -> None:
    outside = tmp_path.parent / f"outside-{time.time_ns()}.pdf"
    outside.write_bytes(b"%PDF secret")
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        stranger = User(name="Other")
        session.add(stranger)
        session.flush()
        mine = Document(
            user_id=user.id,
            kind=DocumentKind.resume,
            label="x",
            is_base=False,
            pdf_path=str(outside),
        )
        theirs = Document(
            user_id=stranger.id,
            kind=DocumentKind.resume,
            label="y",
            is_base=False,
            pdf_path=str(tmp_path / "theirs.pdf"),
        )
        (tmp_path / "theirs.pdf").write_bytes(b"%PDF theirs")
        session.add_all([mine, theirs])
        session.commit()
        ids, stranger_id = (mine.id, theirs.id), stranger.id
    try:
        assert ready.get(f"/api/documents/{ids[0]}/file").status_code == 404  # outside data/
        assert ready.get(f"/api/documents/{ids[1]}/file").status_code == 404  # not yours
    finally:
        with SessionLocal() as session:
            session.delete(session.get(Document, ids[0]))
            session.delete(session.get(User, stranger_id))  # other tests expect one user
            session.commit()


def test_preferences_round_trip(ready) -> None:
    resp = ready.put(
        "/api/preferences",
        json={
            "target_roles": ["Backend Engineer", " backend engineer ", "Platform Engineer"],
            "work_modes": ["remote"],
            "min_salary": 140000,
            "work_authorization": "yes_no_restrictions",
            "requires_sponsorship": "no",
            "jobs_per_day": 3,
            "discovery_time": "08:30",
            "web_search_enabled": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = ready.get("/api/preferences").json()
    assert body["target_roles"] == ["Backend Engineer", "Platform Engineer"]
    assert body["jobs_per_day"] == 3 and body["discovery_time"] == "08:30"
    assert body["requires_sponsorship"] == "no" and body["web_search_enabled"] is False
    assert ready.put("/api/preferences", json={"discovery_time": "25:00"}).status_code == 422
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        assert user.min_salary == 140000
        assert user.application_defaults["work_authorization"] == "yes_no_restrictions"


def test_claude_key_is_checked_and_kept_out_of_responses(ready, monkeypatch) -> None:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    calls: list[str] = []

    async def fake_validate(key: str, workspace_id: str = "") -> None:
        calls.append(key)
        if key.endswith("bad"):
            raise HTTPException(status_code=400, detail="Anthropic didn't accept that key.")

    monkeypatch.setattr(setup_api, "validate_claude_key", fake_validate)
    bad = ready.put("/api/setup/claude-key", json={"api_key": "sk-ant-api03-xxxxxxxxxxxxbad"})
    assert bad.status_code == 400
    assert ready.get("/api/setup").json()["claude_key"] is False

    key = "sk-ant-api03-goodgoodgoodgood"
    ok = ready.put("/api/setup/claude-key", json={"api_key": key})
    assert ok.status_code == 200 and ok.json()["connected"] is True
    assert ok.json()["source"] == "app"
    assert key not in ok.text and key not in ready.get("/api/home").text
    assert get_secret_store().get(ANTHROPIC_API_KEY) == key
    assert ready.get("/api/setup").json()["claude_key"] is True


def test_finish_setup_marks_onboarded_and_searches(ready, monkeypatch) -> None:
    from app.pipeline.discovery import discovery

    started: list[tuple[int, str]] = []
    monkeypatch.setattr(
        discovery,
        "start",
        lambda user_id, trigger="manual": started.append((user_id, trigger)) or True,
    )
    with SessionLocal() as session:
        get_or_create_local_user(session).onboarded_at = None
        session.commit()
    resp = ready.post("/api/setup/finish")
    assert resp.status_code == 200, resp.text
    assert started and started[0][1] == "manual"
    assert ready.get("/api/setup").json()["onboarded"] is True


def test_pause_and_resume(ready) -> None:
    assert ready.post("/api/agent/pause").status_code == 200
    assert is_paused() and ready.get("/api/home").json()["agent"]["paused"] is True
    assert ready.get("/api/home").json()["agent"]["next_search_at"] is None
    assert ready.post("/api/agent/resume").status_code == 200
    assert not is_paused()


def test_home_hides_unscreened_jobs_and_groups_the_rest(ready) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        from app.models import Job

        def add(status, screen=None):
            job = Job(
                source="test", external_id=str(time.time_ns()), url="https://x.test", title="T"
            )
            session.add(job)
            session.flush()
            session.add(Application(user_id=user.id, job_id=job.id, status=status, screen=screen))

        add(ApplicationStatus.matched)
        add(ApplicationStatus.matched, {"decision": "keep", "score": 90})
        add(ApplicationStatus.filtered_out)
        add(ApplicationStatus.submitted)
        session.commit()
    body = ready.get("/api/home").json()
    labels = {row["label"] for row in body["applications"]}
    assert {"Shortlisted", "Not a fit", "Applied"} <= labels
    assert "New" not in labels
    assert body["counts"]["applied"] >= 1 and body["agent"]["shortlisted"] >= 1


def test_org_wide_key_needs_and_sends_a_workspace(ready, monkeypatch) -> None:
    """Organization-wide keys must name a workspace on every request."""

    from app.llm.factory import get_llm_provider
    from app.pipeline.state import set_flag
    from app.services.secrets import WORKSPACE_ID_KEY

    seen: list[tuple[str, str]] = []

    async def fake_validate(key: str, workspace_id: str = "") -> None:
        seen.append((key, workspace_id))
        if not workspace_id:
            raise HTTPException(status_code=400, detail=setup_api.WORKSPACE_HINT)

    monkeypatch.setattr(setup_api, "validate_claude_key", fake_validate)
    try:
        # A new key without a workspace is refused with the explanation.
        org_key = "sk-ant-admin-org-wide-key-0000"
        resp = ready.put("/api/setup/claude-key", json={"api_key": org_key})
        assert resp.status_code == 400 and "workspace" in resp.json()["detail"]
        # Adding only the workspace keeps the current key and checks the pair.
        resp = ready.put("/api/setup/claude-key", json={"workspace_id": "wrkspc_01ABC"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["workspace_id"] == "wrkspc_01ABC"
        assert seen[-1] == ("sk-ant-test-key-000000000000", "wrkspc_01ABC")
        # Every Claude client now carries the header.
        from app.apply.agent import make_client

        assert make_client().default_headers.get("anthropic-workspace-id") == "wrkspc_01ABC"
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        from app.config import get_settings

        get_settings.cache_clear()
        provider = get_llm_provider()
        assert provider._client.default_headers.get("anthropic-workspace-id") == "wrkspc_01ABC"
        bad = ready.put("/api/setup/claude-key", json={"workspace_id": "bad id!"})
        assert bad.status_code == 422
    finally:
        set_flag(WORKSPACE_ID_KEY, "")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        from app.config import get_settings
        from app.llm.factory import reset_llm_provider

        get_settings.cache_clear()
        reset_llm_provider()
