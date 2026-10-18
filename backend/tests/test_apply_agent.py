"""Apply agent tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.apply_agent import (
    _PrepareFormInput,
    _advance_application_form,
    _fill_application_fields,
    prepare_application_form,
    _fill_radio_fields,
    _fill_select_fields,
    _follow_apply_navigation,
    _is_linkedin_job_page,
    _prepare_application_form_sync,
    _try_fill_select,
    _try_open_easy_apply,
    _wait_for_manual_login,
    confirm_application_submit,
)
from app.models import Document, DocumentKind, Job, User


def _mock_sync_playwright(page: MagicMock) -> MagicMock:
    context = MagicMock()
    context.pages = [page]
    chromium = MagicMock()
    chromium.launch_persistent_context.return_value = context
    playwright = MagicMock()
    playwright.chromium = chromium
    sync_playwright = MagicMock()
    sync_playwright.return_value.__enter__.return_value = playwright
    return sync_playwright


def _prepare_payload(*, headless: bool) -> _PrepareFormInput:
    return _PrepareFormInput(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        years_experience=6.0,
        application_defaults={
            "work_authorization": "yes_no_restrictions",
            "requires_sponsorship": "no",
            "referral_source": "LinkedIn Job Posting",
        },
        saved_application_answers={},
        prior_apply_meta=None,
        job_id=1,
        application_id=1,
        job_url="https://www.linkedin.com/jobs/view/123",
        resume_file_path=None,
        resume_content="Tailored resume",
        cover_content="Dear team,\n\nI am excited to apply.",
        account_password="JbTestPass!7a",
        browser_dir=Path("/tmp/browser"),
        screenshots_dir=Path("/tmp/screenshots"),
        headless=headless,
        vision_enabled=False,
    )


def test_is_linkedin_job_page() -> None:
    assert _is_linkedin_job_page("https://www.linkedin.com/jobs/view/123")
    assert not _is_linkedin_job_page("https://boards.greenhouse.io/acme/jobs/1")


def test_try_open_easy_apply_clicks_button() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/123"
    easy_apply = MagicMock()
    easy_apply.count.return_value = 1
    page.get_by_role.return_value = easy_apply
    page.locator.return_value.first.wait_for.return_value = None
    warnings: list[str] = []

    assert _try_open_easy_apply(page, warnings) is True
    easy_apply.first.click.assert_called_once()
    assert any("easy apply" in w.lower() for w in warnings)


def test_follow_apply_navigation_opens_new_tab() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/123"
    context = MagicMock()
    new_page = MagicMock()
    new_page.url = "https://boards.greenhouse.io/acme/jobs/1"
    page_info = MagicMock()
    page_info.value = new_page
    context.expect_page.return_value.__enter__.return_value = page_info
    warnings: list[str] = []

    target = _follow_apply_navigation(page, context, lambda: None, warnings)

    assert target is new_page
    assert any("external application" in w.lower() for w in warnings)


def test_fill_application_fields_skips_stale_elements() -> None:
    page = MagicMock()
    stale = MagicMock()
    stale.evaluate.side_effect = RuntimeError("Execution context was destroyed")
    good = MagicMock()
    good.evaluate.side_effect = ["input", "Email address"]
    good.get_attribute.return_value = ""
    def _query_all(selector: str) -> list:
        if 'input[type="file"]' in selector:
            return []
        return [stale, good]

    page.query_selector_all.side_effect = _query_all

    fields_filled: list[str] = []
    warnings: list[str] = []
    payload = _prepare_payload(headless=False)
    _fill_application_fields(page, payload, fields_filled, warnings)

    assert "email" in fields_filled


def test_fill_application_fields_creates_ats_account_password() -> None:
    page = MagicMock()
    email = MagicMock()
    email.evaluate.side_effect = ["input", "Email Address"]
    email.get_attribute.side_effect = ["text", "", "", "", ""]
    email.fill = MagicMock()
    password = MagicMock()
    password.evaluate.side_effect = ["input", "Password"]
    password.get_attribute.side_effect = ["password", "", "", "", ""]
    password.fill = MagicMock()
    verify = MagicMock()
    verify.evaluate.side_effect = ["input", "Verify New Password"]
    verify.get_attribute.side_effect = ["password", "", "", "", ""]
    verify.fill = MagicMock()

    def _query_all(selector: str) -> list:
        if 'input[type="file"]' in selector:
            return []
        return [email, password, verify]

    page.query_selector_all.side_effect = _query_all
    fields_filled: list[str] = []
    warnings: list[str] = []
    payload = _prepare_payload(headless=False)

    _fill_application_fields(page, payload, fields_filled, warnings)

    email.fill.assert_called_with("lin@example.com")
    password.fill.assert_called_with("JbTestPass!7a")
    verify.fill.assert_called_with("JbTestPass!7a")
    assert "ats_account_password" in fields_filled
    assert any("JbTestPass!7a" in warning for warning in warnings)


def test_fill_application_fields_checks_account_consent() -> None:
    page = MagicMock()
    consent = MagicMock()
    consent.evaluate.side_effect = [
        "Yes, I have read and consent to the terms and conditions",
        "Yes, I have read and consent to the terms and conditions",
    ]
    consent.get_attribute.return_value = ""
    consent.is_checked.return_value = False
    consent.check = MagicMock()

    def _query_all(selector: str) -> list:
        if selector == 'input[type="checkbox"]':
            return [consent]
        if 'input[type="file"]' in selector:
            return []
        return []

    page.query_selector_all.side_effect = _query_all
    fields_filled: list[str] = []
    warnings: list[str] = []
    payload = _prepare_payload(headless=False)

    _fill_application_fields(page, payload, fields_filled, warnings)

    consent.check.assert_called_once()
    assert "account_creation_consent" in fields_filled
    assert any("consent" in warning.lower() for warning in warnings)


def test_try_fill_select_matches_work_authorization() -> None:
    select_el = MagicMock()
    select_el.evaluate.return_value = ["Select one", "Yes, with no restrictions", "No"]
    select_el.scroll_into_view_if_needed = MagicMock()
    select_el.select_option = MagicMock()

    assert _try_fill_select(select_el, "work_authorization", "yes_no_restrictions")
    select_el.select_option.assert_called_with(label="Yes, with no restrictions", timeout=5_000)


def test_fill_select_fields_from_profile_defaults() -> None:
    page = MagicMock()
    select_el = MagicMock()
    select_el.evaluate.side_effect = [
        "Are you legally authorized to work in Canada?",
        ["No", "Yes, with no restrictions"],
    ]
    select_el.scroll_into_view_if_needed = MagicMock()
    select_el.select_option = MagicMock()
    page.query_selector_all.return_value = [select_el]

    fields_filled: list[str] = []
    payload = _prepare_payload(headless=False)
    _fill_select_fields(page, payload, fields_filled)

    assert "work_authorization" in fields_filled


def test_fill_radio_fields_referral_source() -> None:
    page = MagicMock()
    page.evaluate.return_value = ["referral"]
    first = MagicMock()
    page.query_selector.return_value = first
    first.evaluate.return_value = "How did you hear about PocketHealth? *"

    radio = MagicMock()
    radio.count.return_value = 1
    visible = MagicMock()
    visible.is_visible.return_value = True
    visible.scroll_into_view_if_needed = MagicMock()
    visible.check = MagicMock()
    radio.first = visible
    page.get_by_role.return_value = radio
    page.wait_for_timeout = MagicMock()

    fields_filled: list[str] = []
    payload = _prepare_payload(headless=False)
    _fill_radio_fields(page, payload, fields_filled)

    assert any("referral_source" in key for key in fields_filled)
    visible.check.assert_called_once()


def test_advance_application_form_delegates_to_harness() -> None:
    page = MagicMock()
    fields: list[str] = []
    warnings: list[str] = []
    harness_result = MagicMock()
    harness_result.submit_selector = 'button:has-text("Submit")'
    harness_result.session_dir = "/tmp/sessions/1"

    with patch("app.agents.apply_agent.BrowserHarness") as harness_cls:
        harness_cls.return_value.run_until_ready.return_value = harness_result
        result = _advance_application_form(page, _prepare_payload(headless=False), fields, warnings)

    assert result == 'button:has-text("Submit")'
    harness_cls.return_value.run_until_ready.assert_called_once()
    assert any("session trace" in w.lower() for w in warnings)


def test_wait_for_manual_login_waits_when_on_login_wall() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/login"
    body = MagicMock()
    body.inner_text.return_value = "Sign in to LinkedIn. Join LinkedIn."
    page.query_selector.return_value = body
    warnings: list[str] = []

    with patch("app.agents.apply_agent._on_login_wall", return_value=True):
        _wait_for_manual_login(page, "https://www.linkedin.com/jobs/view/123", warnings)

    page.wait_for_function.assert_called_once()
    assert any("sign in" in w.lower() for w in warnings)


def test_prepare_waits_for_user_close_when_headed() -> None:
    page = MagicMock()
    page.url = "https://www.linkedin.com/jobs/view/123"
    page.query_selector_all.return_value = []
    page.query_selector.return_value = None
    sync_playwright = _mock_sync_playwright(page)
    context = sync_playwright.return_value.__enter__.return_value.chromium.launch_persistent_context.return_value

    with (
        patch("playwright.sync_api.sync_playwright", sync_playwright),
        patch("app.agents.apply_agent._wait_for_manual_login"),
        patch("app.agents.apply_agent._start_apply_flow", return_value=(page, "easy_apply")),
        patch("app.agents.apply_agent._advance_application_form", return_value=None),
    ):
        result = _prepare_application_form_sync(_prepare_payload(headless=False))

    page.bring_to_front.assert_called_once()
    page.wait_for_event.assert_called_once_with("close", timeout=0)
    context.close.assert_called_once()
    assert result.apply_flow == "easy_apply"
    assert any("close the browser window" in w.lower() for w in result.warnings)


@pytest.mark.asyncio
async def test_prepare_reuses_prior_generated_account_password() -> None:
    user = User(name="Lin", email="lin@example.com")
    job = Job(id=1, source="test", external_id="1", url="https://jobs.example.com/1", title="Role")
    resume = Document(id=1, user_id=1, kind=DocumentKind.resume, label="Resume", content="Resume")
    prior = {"generated_account_password": "JbExisting!7a"}

    with patch("app.agents.apply_agent.run_playwright_in_thread") as run_thread:
        run_thread.return_value = None
        await prepare_application_form(
            user,
            job,
            resume_doc=resume,
            cover_doc=None,
            browser_dir=Path("/tmp/browser"),
            screenshots_dir=Path("/tmp/screenshots"),
            prior_apply_meta=prior,
        )

    payload = run_thread.call_args.args[1]
    assert payload.account_password == "JbExisting!7a"


@pytest.mark.asyncio
async def test_prepare_uses_browser_use_when_configured() -> None:
    user = User(name="Lin", email="lin@example.com")
    job = Job(id=1, source="test", external_id="1", url="https://jobs.rbc.com/1", title="Role")
    resume = Document(id=1, user_id=1, kind=DocumentKind.resume, label="Resume", content="Resume")
    settings = MagicMock()
    settings.apply_vision_enabled = True
    settings.use_browser_use_apply.return_value = True
    browser_use_result = MagicMock()
    browser_use_result.page_url = "https://jobs.rbc.com/apply/review"
    browser_use_result.fields_filled = ["email", "resume_upload"]
    browser_use_result.submit_selector = "Submit application"
    browser_use_result.browser_use_session_id = "session-123"
    browser_use_result.browser_use_live_url = "https://cloud.browser-use.com/live/session-123"
    browser_use_result.browser_use_output = {"status": "ready_for_user_approval"}
    browser_use_result.browser_use_mode = "local"
    browser_use_result.generated_account_password = None
    browser_use_result.warnings = ["Browser Use summary: ready"]

    with (
        patch("app.agents.apply_agent.get_settings", return_value=settings),
        patch(
            "app.agents.apply_agent.run_browser_use_apply",
            new=AsyncMock(return_value=browser_use_result),
        ) as run_browser_use,
        patch("app.agents.apply_agent.run_playwright_in_thread") as run_thread,
    ):
        result = await prepare_application_form(
            user,
            job,
            resume_doc=resume,
            cover_doc=None,
            browser_dir=Path("/tmp/browser"),
            screenshots_dir=Path("/tmp/screenshots"),
        )

    run_browser_use.assert_awaited_once()
    run_thread.assert_not_called()
    assert result.browser_engine == "browser_use"
    assert result.apply_flow == "browser_use"
    assert result.browser_use_session_id == "session-123"
    assert result.browser_use_live_url == "https://cloud.browser-use.com/live/session-123"
    assert result.browser_use_mode == "local"


@pytest.mark.asyncio
async def test_confirm_submit_uses_browser_use_session() -> None:
    with patch(
        "app.agents.apply_agent.confirm_browser_use_submit",
        new=AsyncMock(return_value=(True, "submitted")),
    ) as confirm:
        clicked, warning = await confirm_application_submit(
            page_url="https://jobs.rbc.com/apply/review",
            submit_selector=None,
            browser_dir=Path("/tmp/browser"),
            browser_use_session_id="session-123",
        )

    confirm.assert_awaited_once_with("session-123")
    assert clicked is True
    assert warning == "submitted"
