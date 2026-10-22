"""Apply-run lifecycle through the API and chat, with scripted stand-ins for browser + Claude.

Real: the queue, statuses, site accounts, chat prompts/buttons, the submit decision,
revisions, expiry and scorecard. Faked: the browser session/driver and the Claude
agent loop (each test scripts what "Claude" does through the run's hooks).
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.tools import ToolError
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import documents as documents_api
from app.api.profile import get_or_create_local_user
from app.apply import runs as runs_module
from app.apply import scorecard as scorecard_module
from app.apply.accounts import account_password
from app.apply.agent import RunStats, request_params
from app.apply.runs import run_manager
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Application,
    ApplicationStatus,
    ApplyRun,
    Job,
    MessagingLink,
    SiteAccount,
    User,
)
from app.pipeline.prefs import load_preferences, save_preferences

REVIEW_URL = "https://acme.wd3.myworkdayjobs.com/en-US/External/job/123/apply/review"
HOST = "acme.wd3.myworkdayjobs.com"
ANSWERS = [
    {"field": "Email", "value": "lin@example.com", "source": "profile", "note": None},
    {"field": "Notice period", "value": "2 weeks", "source": "judgement", "note": "default"},
    {"field": "Terms", "value": "Accepted", "source": "consent", "note": "Privacy statement"},
]


class FakeSession:
    agent_focus_target_id = "tab-1"
    is_cdp_connected = True

    async def navigate_to(self, url, new_tab=False):
        self.url = url

    async def close_page(self, tab_id):
        pass

    async def kill(self):
        pass


class FakeDriver:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.clicks = 0

    async def __aenter__(self):
        return self

    async def close(self):
        pass

    def pin_to_tab(self, tab_id):
        self.tab = tab_id

    async def screenshot_png(self, tab_id, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG fake")
        return REVIEW_URL

    def network_mark(self):
        return {}

    def requests_since(self, mark):
        return [
            {"method": "POST", "url": "https://acme.wd3.myworkdayjobs.com/submit", "status": 200}
        ]

    async def click_final_submit(self, tab_id, reviewed_url):
        self.clicks += 1
        assert reviewed_url == REVIEW_URL
        return self.behaviour.get("click", (True, "Submit application"))

    async def page_text(self, tab_id):
        return REVIEW_URL, self.behaviour.get("page_text", "Thank you for applying!")


@pytest.fixture
def scripted(tmp_path, monkeypatch):
    """Patch the browser/Claude boundaries; returns knobs for the scripted agent.

    `behaviour["script"]`: "plain" (just finish), "account" (create a site account
    first) or "captcha" (ask for a human; set aside unless the run is attended).
    """

    behaviour: dict = {"script": "plain", "finish": "ready_for_review", "tasks": []}
    monkeypatch.setattr(runs_module, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(scorecard_module, "SPIKE_DIR", tmp_path / "spike")
    monkeypatch.setattr(scorecard_module, "SCORECARD", tmp_path / "spike" / "scorecard.jsonl")
    monkeypatch.setattr(runs_module, "_CONFIRM_WAIT_S", 0.2)
    monkeypatch.setattr(documents_api, "SERVE_ROOTS", [tmp_path])

    async def fake_preflight(client, model, effort):
        return None

    async def fake_browser():
        return FakeSession()

    drivers: list[FakeDriver] = []

    def fake_guarded_driver(session, uploads_dir, hooks):
        assert any(Path(uploads_dir).iterdir()), "a resume should be staged"
        driver = FakeDriver(behaviour)
        drivers.append(driver)
        return driver

    async def fake_run_agent(client, driver, *, task, ats, config, hooks):
        behaviour["tasks"].append(task)
        hooks.event("tool_call", {"iteration": 1, "tool": "browser.navigate", "input": {}})
        if task.startswith("The application in this tab is already filled"):
            return RunStats(
                iterations=2,
                cost_usd=0.05,
                finish={"status": "ready_for_review", "summary": "Changed it.", "answers": []},
            )
        script = behaviour["script"]
        if script == "account":
            try:
                behaviour["login"] = hooks.site_login(HOST, "create")
            except ToolError as e:
                behaviour["refused"] = str(e)
                return RunStats(finish={"status": "needs_you", "summary": "Needs an account."})
        if script == "captcha":
            reply = await hooks.request_human("captcha", "Solve the CAPTCHA on the sign-up page")
            behaviour.setdefault("replies", []).append(reply)
            if reply is None:
                return RunStats(
                    iterations=4,
                    finish={"status": "needs_you", "summary": "Solve the CAPTCHA on sign-up."},
                )
        if behaviour["finish"] is None:
            return RunStats(iterations=3, error="Claude stopped without finishing (end_turn).")
        return RunStats(
            iterations=7,
            cost_usd=0.42,
            finish={
                "status": behaviour["finish"],
                "summary": "Filled every page.",
                "answers": ANSWERS,
                "fields_needing_review": ["Salary"],
            },
        )

    closed_tabs: list[str] = []

    async def fake_close_tab(tab_id):
        closed_tabs.append(tab_id)

    monkeypatch.setattr(runs_module, "make_client", lambda: object())
    monkeypatch.setattr(runs_module, "preflight", fake_preflight)
    monkeypatch.setattr(run_manager, "_browser", fake_browser)
    monkeypatch.setattr(run_manager, "_session", FakeSession())
    monkeypatch.setattr(run_manager, "_close_tab", fake_close_tab)
    monkeypatch.setattr(runs_module, "guarded_driver", fake_guarded_driver)
    monkeypatch.setattr(runs_module, "run_agent", fake_run_agent)
    behaviour["drivers"] = drivers
    behaviour["closed_tabs"] = closed_tabs
    behaviour["scorecard"] = tmp_path / "spike" / "scorecard.jsonl"
    return behaviour


def _seed(client: TestClient) -> int:
    client.post(
        "/api/documents",
        data={"kind": "resume"},
        files={"file": ("resume.txt", io.BytesIO(b"Lin Ho - backend engineer"), "text/plain")},
    )
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.email = "lin@example.com"
        save_preferences(user, load_preferences(user))
        job = Job(
            source="test",
            external_id=f"run-{time.time_ns()}",
            url=f"https://{HOST}/en-US/External/job/123",
            title="Backend Engineer",
            company="Acme",
        )
        session.add(job)
        session.flush()
        application = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.approved)
        session.add(application)
        session.execute(delete(SiteAccount))
        session.commit()
        return application.id


def _wait(client: TestClient, run_id: int, status: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/apply-runs/{run_id}").json()
        if body["run"]["status"] == status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {status!r}: {body.get('run')}")


def _app_status(client: TestClient, application_id: int) -> str:
    return client.get(f"/api/applications/{application_id}").json()["status"]


def _card(client: TestClient, kind: str) -> dict | None:
    return next((c for c in client.get("/api/home").json()["needs_you"] if c["type"] == kind), None)


def test_unattended_run_makes_an_account_then_review_change_and_submit(scripted) -> None:
    scripted["script"] = "account"
    with TestClient(app) as client:
        app_id = _seed(client)
        started = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        again = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert again.status_code in (400, 409)  # already running, or already parked

        parked = _wait(client, run_id, "awaiting_submit")
        assert parked["run"]["answers"] == ANSWERS
        assert parked["run"]["expires_at"] is not None
        assert _app_status(client, app_id) == "awaiting_submit"

        # The account: made with the profile email, password in the keyring only.
        login, password = scripted["login"]
        assert login == "lin@example.com" and len(password) == 20
        with SessionLocal() as session:
            user_id = get_or_create_local_user(session).id
            account = session.execute(select(SiteAccount)).scalar_one()
        assert (account.host, account.login, account.status) == (HOST, login, "created")
        assert account_password(user_id, HOST) == password
        events = [e["kind"] for e in parked["events"]]
        assert "account_created" in events
        assert password not in json.dumps(parked)
        assert "No saved account" not in scripted["tasks"][0]
        assert "JobBee has no account on this job's site yet" in scripted["tasks"][0]
        assert "Accept terms and privacy statements" in scripted["tasks"][0]

        shot = client.get(f"/api/apply-runs/{run_id}/screenshot")
        assert shot.status_code == 200 and shot.content.endswith(b"PNG fake")
        review = _card(client, "review")
        assert review["run_id"] == run_id and review["checks"] == ["Salary"]
        assert review["answers"][1]["source"] == "judgement" and review["updating"] is False

        # The user corrects the agent's judgement call before submitting.
        change = {"changes": [{"field": "Notice period", "value": "1 month"}]}
        assert client.post(f"/api/apply-runs/{run_id}/revise", json=change).status_code == 202
        assert client.post(f"/api/apply-runs/{run_id}/revise", json=change).status_code in (
            202,
            409,
        )
        revised = _wait(client, run_id, "awaiting_submit")
        assert scripted["tasks"][-1].startswith("The application in this tab is already filled")
        assert '"Notice period": 1 month' in scripted["tasks"][-1]
        notice = next(a for a in revised["run"]["answers"] if a["field"] == "Notice period")
        assert notice == {
            "field": "Notice period",
            "value": "1 month",
            "source": "you",
            "note": None,
        }
        assert revised["run"]["error"] is None

        decided = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "submitted"
        assert decided.json()["confirmation"]["text"].lower().startswith("thank you for applying")
        assert _app_status(client, app_id) == "submitted"
        assert (
            client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True}).status_code
            == 409
        )
        assert scripted["drivers"][0].clicks == 1
        assert scripted["closed_tabs"] == ["tab-1"]  # applied: its tab is closed

        detail = client.get(f"/api/applications/{app_id}").json()
        assert detail["runs"][0]["answers"][1]["value"] == "1 month"

    with SessionLocal() as session:
        user = session.execute(select(User).order_by(User.id)).scalars().first()
        saved = user.application_defaults["saved_answers"]
        # Confirmed by Submit: the correction is remembered; profile answers aren't copied.
        assert saved["notice period"] == "1 month"
        assert "email" not in saved and "terms" not in saved
    row = json.loads(scripted["scorecard"].read_text().splitlines()[-1])
    assert row["outcome"] == "submitted" and row["reached_review"] is True


def test_handoff_sets_aside_then_attended_retry_waits_for_the_user(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]

        set_aside = _wait(client, run_id, "needs_you")
        assert scripted["replies"] == [None]  # nobody was asked; the run ended at once
        assert _app_status(client, app_id) == "approved"
        card = _card(client, "set_aside")
        assert card["run_id"] == run_id and "CAPTCHA" in card["message"]
        assert set_aside["run"]["attended"] is False

        attended = client.post(f"/api/apply-runs/{run_id}/attend")
        assert attended.status_code == 200, attended.text
        retry_id = attended.json()["id"]
        assert retry_id != run_id and attended.json()["attended"] is True
        assert client.post(f"/api/apply-runs/{run_id}/attend").status_code == 409

        waiting = _wait(client, retry_id, "needs_human")
        assert waiting["run"]["pending"]["kind"] == "captcha"
        assert _card(client, "set_aside") is None
        assert _card(client, "handoff")["run_id"] == retry_id
        assert client.post(f"/api/apply-runs/{retry_id}/resume", json={}).is_success

        _wait(client, retry_id, "awaiting_submit")
        assert scripted["replies"] == [None, ""]
        client.post(f"/api/apply-runs/{retry_id}/decision", json={"submit": False})


def test_set_aside_application_can_be_skipped(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        assert client.post(f"/api/apply-runs/{run_id}/dismiss").json()["status"] == "discarded"
        assert _app_status(client, app_id) == "rejected_by_user"
        assert client.post(f"/api/apply-runs/{run_id}/attend").status_code == 409


def test_accounts_need_the_users_permission(scripted) -> None:
    scripted["script"] = "account"
    with TestClient(app) as client:
        app_id = _seed(client)
        assert client.put("/api/preferences", json={"agent_accounts": False}).is_success
        assert client.get("/api/preferences").json()["agent_accounts"] is False
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        assert "hasn't allowed" in scripted["refused"]
        assert "create job-site accounts and sign in with `site_account`: not allowed" in (
            scripted["tasks"][0].lower()
        )
        assert client.put("/api/preferences", json={"agent_accounts": True}).is_success
    with SessionLocal() as session:
        assert session.execute(select(SiteAccount)).first() is None


def test_dont_submit_closes_and_marks_skipped(scripted) -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "awaiting_submit")
        resp = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": False})
        assert resp.json()["status"] == "discarded"
        assert _app_status(client, app_id) == "rejected_by_user"
        assert scripted["drivers"][0].clicks == 0
        late = client.post(
            f"/api/apply-runs/{run_id}/revise", json={"changes": [{"field": "a", "value": "b"}]}
        )
        assert late.status_code == 409


def test_unconfirmed_submit_asks_the_user(scripted) -> None:
    scripted["click"] = (False, "no final submit button found")
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "awaiting_submit")
        resp = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert resp.json()["status"] == "submit_unconfirmed"
        assert _app_status(client, app_id) == "awaiting_submit"
        assert scripted["closed_tabs"] == []  # left open for the user to check
        ok = client.post(f"/api/apply-runs/{run_id}/outcome", json={"went_through": True})
        assert ok.json()["status"] == "submitted"
        assert _app_status(client, app_id) == "submitted"


def test_parked_form_expires(scripted, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "submit_ttl_min", 0.003)  # ~0.2s
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "expired")
        assert _app_status(client, app_id) == "approved"
        late = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert late.status_code == 409


def test_blocked_run_marks_failed(scripted) -> None:
    scripted["finish"] = "blocked"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "blocked")
        assert _app_status(client, app_id) == "failed"
        detail = client.get(f"/api/applications/{app_id}").json()
        assert detail["group"] == "didnt_finish" and detail["can_retry"] is True
        retried = client.post(f"/api/applications/{app_id}/retry")
        assert retried.status_code == 200, retried.text
        _wait(client, retried.json()["run_id"], "blocked")
        assert scripted["closed_tabs"][0] == "tab-1"  # the first try's tab was closed


def test_cancel_while_waiting_on_the_user(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        retry_id = client.post(f"/api/apply-runs/{run_id}/attend").json()["id"]
        _wait(client, retry_id, "needs_human")
        assert client.post(f"/api/apply-runs/{retry_id}/cancel").is_success
        cancelled = _wait(client, retry_id, "cancelled")
        assert cancelled["run"]["pending"] is None
        assert scripted["closed_tabs"] == ["tab-1"]  # the set-aside one; a stopped run keeps it
        again = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert again.status_code == 202
        client.post(f"/api/apply-runs/{again.json()['id']}/cancel")


def test_chat_reply_finishes_a_handoff(scripted) -> None:
    """A free-text Telegram reply resolves an attended run's hand-off."""

    from app.messaging.service import messaging

    scripted["script"] = "captcha"
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()
    with TestClient(app) as client:
        app_id = _seed(client)
        with SessionLocal() as session:
            user_id = get_or_create_local_user(session).id
            session.add(
                MessagingLink(user_id=user_id, channel="telegram", chat_id="77", from_id="77")
            )
            session.commit()
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        retry_id = client.post(f"/api/apply-runs/{run_id}/attend").json()["id"]
        _wait(client, retry_id, "needs_human")

        update = {
            "message": {"message_id": 5, "chat": {"id": 77}, "from": {"id": 77}, "text": "done"}
        }
        client.portal.call(messaging.router.handle_update, update)
        _wait(client, retry_id, "awaiting_submit")
        assert scripted["replies"] == [None, ""]
        client.post(f"/api/apply-runs/{retry_id}/decision", json={"submit": False})
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()


def test_start_requires_approved_application(scripted) -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
        with SessionLocal() as session:
            session.get(Application, app_id).status = ApplicationStatus.matched
            session.commit()
        resp = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert resp.status_code == 400


def test_restart_fails_fills_and_expires_parked_forms() -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
    with SessionLocal() as session:
        session.get(Application, app_id).status = ApplicationStatus.awaiting_submit
        rows = [
            ApplyRun(application_id=app_id, status=status, url="https://x", model="m", effort="low")
            for status in ("needs_human", "awaiting_submit", "revising")
        ]
        session.add_all(rows)
        session.commit()
        ids = [r.id for r in rows]

    assert run_manager.mark_interrupted_runs() >= 3
    with SessionLocal() as session:
        assert [session.get(ApplyRun, i).status for i in ids] == ["failed", "expired", "expired"]
        assert session.get(Application, app_id).status == ApplicationStatus.approved


@pytest.mark.asyncio
async def test_closing_the_last_tab_quits_chrome() -> None:
    calls: list[str] = []

    class TabsSession:
        def __init__(self, tabs: list[str]) -> None:
            targets = [SimpleNamespace(target_id=t) for t in tabs]
            self.session_manager = SimpleNamespace(get_all_page_targets=lambda: targets)

        async def close_page(self, tab_id: str) -> None:
            calls.append(f"close {tab_id}")

        async def kill(self) -> None:
            calls.append("quit")

    manager = runs_module.RunManager()
    manager._session = TabsSession(["T1", "T2"])  # another form is still open
    await manager._close_tab("T2")
    assert manager._session is not None
    manager._session = TabsSession(["T1"])
    await manager._close_tab("T1")
    assert calls == ["close T2", "quit"]
    assert manager._session is None  # the next run opens Chrome again


def test_requests_keep_the_cached_history_intact() -> None:
    params = request_params("claude-opus-5-5", "medium", "rules")
    assert "context_management" not in params
    assert params["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_agent_config_reports_models() -> None:
    with TestClient(app) as client:
        body = client.get("/api/apply-runs/config").json()
    assert "claude-opus-5-5" in body["models"]
    assert body["default_effort"] in body["efforts"]
