"""Browser Use apply runner tests."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.agents.browser_use_apply_runner import (
    BrowserUseApplyError,
    BrowserUseApplyInput,
    _autofill_known_fields,
    _apply_task,
    _auto_open_live_browser,
    _cloud_model_name,
    _known_autofill_values,
    _local_model_name,
    _progress_known_account_page,
    _resolve_cloud_workspace_id,
    _run_cloud_browser_use_apply,
    _run_local_browser_use_apply,
    _run_local_browser_use_apply_safe,
)


def _payload() -> BrowserUseApplyInput:
    return BrowserUseApplyInput(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={"linkedin": "https://linkedin.com/in/lin"},
        years_experience=6,
        application_defaults={"requires_sponsorship": "no"},
        saved_application_answers={},
        job_id=1,
        application_id=40,
        job_url="https://jobs.rbc.com/role",
        resume_file_path=None,
        resume_content="resume",
        cover_content=None,
        account_password="JbTempPass!7a",
        browser_dir=Path("/tmp/browser"),
        screenshots_dir=Path("/tmp/screenshots"),
        headless=False,
    )


def test_apply_task_has_job_application_safety_rules() -> None:
    prompt = _apply_task(_payload(), ["resume.txt"])

    assert "Never click Submit" in prompt
    assert "Stop at the final review/submit screen" in prompt
    assert "Create account" in prompt
    assert "JbTempPass!7a" in prompt
    assert "Do not loop" in prompt
    assert "pre-navigated" in prompt
    assert "If a Create Account, Register, or Sign Up page is visible, stay on that page" in prompt
    assert "On SAP SuccessFactors" in prompt
    assert "do not retry the same sign-in" in prompt
    assert "Known visible fields may already be filled by JobBee" in prompt


def test_known_autofill_values_include_account_fields() -> None:
    payload = _payload()
    payload.user_name = "Lin Hoang"
    payload.application_defaults = {
        "phone": "+1 416 555 0100",
        "phone_extension": "123",
        "phone_device_type": "Mobile",
        "country_phone_code": "Canada (+1)",
        "address_line1": "123 Main St",
        "address_line2": "Unit 4",
        "city": "Toronto",
        "province": "ON",
        "postal_code": "M5V 1A1",
        "work_country": "Canada",
        "referral_source": "LinkedIn Job Posting",
    }
    payload.user_links = {
        "linkedin": "https://linkedin.com/in/lin",
        "github": "https://github.com/lin",
    }

    values = _known_autofill_values(payload)

    assert values["email"] == "lin@example.com"
    assert values["password"] == "JbTempPass!7a"
    assert values["first_name"] == "Lin"
    assert values["last_name"] == "Hoang"
    assert values["phone"] == "+1 416 555 0100"
    assert values["phone_extension"] == "123"
    assert values["phone_device_type"] == "Mobile"
    assert values["country_phone_code"] == "Canada (+1)"
    assert values["address_line1"] == "123 Main St"
    assert values["address_line2"] == "Unit 4"
    assert values["city"] == "Toronto"
    assert values["state"] == "ON"
    assert values["postal_code"] == "M5V 1A1"
    assert values["country"] == "Canada"
    assert values["prior_employer"] == "No"
    assert values["linkedin"] == "https://linkedin.com/in/lin"


def test_autofill_script_keeps_phone_extension_separate() -> None:
    from app.agents.browser_use_apply_runner import _AUTOFILL_KNOWN_FIELDS_JS

    assert "phone_extension" in _AUTOFILL_KNOWN_FIELDS_JS
    assert "clear: !values.phone_extension" in _AUTOFILL_KNOWN_FIELDS_JS
    assert "prior_employer" in _AUTOFILL_KNOWN_FIELDS_JS


@pytest.mark.asyncio
async def test_autofill_known_fields_uses_current_page() -> None:
    page = MagicMock()
    page.evaluate = AsyncMock(return_value='{"filled":["email","confirm_email","password"]}')
    browser_session = MagicMock()
    browser_session.get_current_page = AsyncMock(return_value=page)
    agent = MagicMock()
    agent.browser_session = browser_session

    filled = await _autofill_known_fields(agent, _payload())

    assert filled == ["email", "confirm_email", "password"]
    page.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_workspace_id_prefers_configured_value() -> None:
    settings = Settings(browser_use_workspace_id="ws-configured")
    client = MagicMock()
    client.workspaces.list = AsyncMock()
    client.workspaces.create = AsyncMock()

    with patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings):
        workspace_id = await _resolve_cloud_workspace_id(client, _payload())

    assert workspace_id == "ws-configured"
    client.workspaces.list.assert_not_called()
    client.workspaces.create.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_workspace_id_reuses_first_existing_workspace() -> None:
    settings = Settings(browser_use_workspace_id="", browser_use_allow_workspace_create=False)
    client = MagicMock()
    client.workspaces.list = AsyncMock(
        return_value={
            "items": [
                {"id": "ws-newer", "name": "newer", "createdAt": "2026-01-01T00:00:00Z"},
                {"id": "ws-first", "name": "first", "createdAt": "2025-01-01T00:00:00Z"},
            ]
        }
    )
    client.workspaces.create = AsyncMock()

    with patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings):
        workspace_id = await _resolve_cloud_workspace_id(client, _payload())

    assert workspace_id == "ws-first"
    client.workspaces.list.assert_awaited_once_with(page_size=100, page_number=1)
    client.workspaces.create.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_workspace_id_does_not_create_by_default() -> None:
    settings = Settings(browser_use_workspace_id="", browser_use_allow_workspace_create=False)
    client = MagicMock()
    client.workspaces.list = AsyncMock(return_value={"items": []})
    client.workspaces.create = AsyncMock()

    with patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings):
        with pytest.raises(BrowserUseApplyError, match="BROWSER_USE_WORKSPACE_ID"):
            await _resolve_cloud_workspace_id(client, _payload())

    client.workspaces.create.assert_not_called()


@pytest.mark.asyncio
async def test_cloud_browser_use_attaches_existing_workspace_to_session_and_run() -> None:
    settings = Settings(
        apply_browser_engine="browser_use_cloud",
        browseruse_api_key="bu-test",
        browser_use_model="bu-max",
        browser_use_timeout_s=5,
    )
    payload = _payload()
    client = MagicMock()
    client.sessions.create = AsyncMock(
        return_value={"id": "session-1", "live_url": "https://live.browser-use/session-1"}
    )
    client.run = AsyncMock(
        return_value={
            "id": "session-1",
            "live_url": "https://live.browser-use/session-1",
            "output": {
                "status": "ready_for_user_approval",
                "current_url": "https://jobs.example/review",
                "fields_filled": ["email"],
                "final_submit_visible": True,
                "final_submit_label": "Submit application",
                "summary": "Ready for review.",
            },
        }
    )
    client_cls = MagicMock(return_value=client)

    with (
        patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings),
        patch("app.agents.browser_use_apply_runner._import_browser_use_client", return_value=client_cls),
        patch(
            "app.agents.browser_use_apply_runner._resolve_cloud_workspace_id",
            new=AsyncMock(return_value="ws-existing"),
        ),
        patch(
            "app.agents.browser_use_apply_runner._upload_workspace_files",
            new=AsyncMock(return_value=["resume.txt"]),
        ),
        patch("app.agents.browser_use_apply_runner._auto_open_live_browser", return_value=False),
    ):
        result = await _run_cloud_browser_use_apply(payload)

    client.sessions.create.assert_awaited_once_with(
        keep_alive=True,
        workspace_id="ws-existing",
        model="bu-max",
    )
    _, run_kwargs = client.run.await_args
    assert run_kwargs["session_id"] == "session-1"
    assert run_kwargs["workspace_id"] == "ws-existing"
    assert run_kwargs["model"] == "bu-max"
    assert result.browser_use_mode == "cloud"
    assert result.browser_use_session_id == "session-1"
    assert result.submit_selector == "Submit application"


@pytest.mark.asyncio
async def test_progress_known_account_page_uses_current_page() -> None:
    mouse = MagicMock()
    mouse.move = AsyncMock()
    mouse.click = AsyncMock()

    class DummyPage:
        def __init__(self) -> None:
            self.evaluate = AsyncMock(
                side_effect=[
                    (
                        '{"account_page":true,"clicked":false,'
                        '"actions":["profile_visibility_restricted","account_create_button_ready"],'
                        '"click":{"x":420,"y":640}}'
                    ),
                    '{"account_page":false,"account_button_visible":false,"errors":[]}',
                ]
            )

        @property
        async def mouse(self):
            return mouse

    page = DummyPage()
    browser_session = MagicMock()
    browser_session.get_current_page = AsyncMock(return_value=page)
    browser_session.get_current_page_url = AsyncMock(
        side_effect=["https://ats.example/create-account", "https://ats.example/application"]
    )
    agent = MagicMock()
    agent.browser_session = browser_session

    actions = await _progress_known_account_page(agent)

    assert actions == [
        "profile_visibility_restricted",
        "account_create_button_ready",
        "account_create_real_click",
    ]
    mouse.move.assert_awaited_once_with(420, 640)
    mouse.click.assert_awaited_once_with(420, 640)
    assert page.evaluate.await_count == 2


@pytest.mark.asyncio
async def test_progress_known_account_page_reports_no_advance() -> None:
    mouse = MagicMock()
    mouse.move = AsyncMock()
    mouse.click = AsyncMock()

    class DummyPage:
        def __init__(self) -> None:
            self.evaluate = AsyncMock(
                side_effect=[
                    '{"account_page":true,"actions":["account_create_button_ready"],"click":{"x":420,"y":640}}',
                    '{"account_page":true,"account_button_visible":true,"errors":["Email is required"]}',
                ]
            )

        @property
        async def mouse(self):
            return mouse

    page = DummyPage()
    browser_session = MagicMock()
    browser_session.get_current_page = AsyncMock(return_value=page)
    browser_session.get_current_page_url = AsyncMock(return_value="https://ats.example/create-account")
    agent = MagicMock()
    agent.browser_session = browser_session

    actions = await _progress_known_account_page(agent)

    assert actions == ["account_create_button_ready", "account_create_click_no_advance"]


def test_local_litellm_adapter_dependency_is_available() -> None:
    import litellm  # noqa: F401

    from app.agents.browser_use_apply_runner import _local_llm
    from app.config import Settings

    llm = _local_llm(Settings(llm_provider="ollama", llm_model="gemma4-e4b-agent:latest"))

    assert llm is not None


def test_local_model_name_preserves_ollama_tag() -> None:
    from app.config import Settings

    assert (
        _local_model_name(
            Settings(llm_provider="ollama", llm_model="gemma4-e4b-agent:latest")
        )
        == "ollama/gemma4-e4b-agent:latest"
    )


def test_local_model_name_prefixes_openrouter_provider() -> None:
    from app.config import Settings

    assert (
        _local_model_name(
            Settings(
                llm_provider="openai",
                llm_base_url="https://openrouter.ai/api/v1",
                openrouter_api_key="sk-test",
                openrouter_model="nex-agi/nex-n2-pro:free",
                llm_model="nex-agi/nex-n2-pro:free",
            )
        )
        == "openrouter/nex-agi/nex-n2-pro:free"
    )


def test_local_model_name_keeps_prefixed_browser_use_override() -> None:
    from app.config import Settings

    assert (
        _local_model_name(
            Settings(
                llm_provider="openai",
                llm_base_url="https://openrouter.ai/api/v1",
                browser_use_model="openrouter/nex-agi/nex-n2-pro:free",
            )
        )
        == "openrouter/nex-agi/nex-n2-pro:free"
    )


def test_cloud_model_name_defaults_to_browser_use_model() -> None:
    assert _cloud_model_name(Settings(browser_use_model="")) == "bu-max"


def test_cloud_model_name_rejects_openrouter_or_unknown_model() -> None:
    assert (
        _cloud_model_name(Settings(browser_use_model="openrouter/google/gemma-4-31b-it:free"))
        == "bu-max"
    )
    assert _cloud_model_name(Settings(browser_use_model="claude-opus-4.7")) == "bu-max"


def test_cloud_model_name_accepts_browser_use_cloud_enum() -> None:
    assert _cloud_model_name(Settings(browser_use_model="claude-opus-4.6")) == "claude-opus-4.6"
    assert _cloud_model_name(Settings(browser_use_model="browser-use/bu-mini")) == "bu-mini"


def test_apply_task_waits_for_linkedin_login() -> None:
    prompt = _apply_task(_payload(), ["resume.txt"])

    assert "If LinkedIn login is required" in prompt
    assert "wait" in prompt
    assert "Do not report failure immediately" in prompt


def test_auto_open_live_browser_in_dev() -> None:
    settings = MagicMock()
    settings.is_dev = True
    settings.browser_use_auto_open_live = True

    with (
        patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings),
        patch("app.agents.browser_use_apply_runner.webbrowser.open", return_value=True) as open_browser,
    ):
        assert _auto_open_live_browser("https://live.browser-use.com/session/123") is True

    open_browser.assert_called_once_with(
        "https://live.browser-use.com/session/123",
        new=2,
        autoraise=True,
    )


def test_auto_open_live_browser_disabled_outside_dev() -> None:
    settings = MagicMock()
    settings.is_dev = False
    settings.browser_use_auto_open_live = True

    with (
        patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings),
        patch("app.agents.browser_use_apply_runner.webbrowser.open") as open_browser,
    ):
        assert _auto_open_live_browser("https://live.browser-use.com/session/123") is False

    open_browser.assert_not_called()


@pytest.mark.asyncio
async def test_local_browser_use_runs_in_thread_on_windows() -> None:
    result = MagicMock()

    with (
        patch("app.agents.browser_use_apply_runner.sys.platform", "win32"),
        patch(
            "app.agents.browser_use_apply_runner.asyncio.to_thread",
            new=AsyncMock(return_value=result),
        ) as to_thread,
    ):
        assert await _run_local_browser_use_apply_safe(_payload()) is result

    to_thread.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_browser_use_uses_fast_ollama_settings(tmp_path) -> None:
    payload = _payload()
    payload.browser_dir = tmp_path / "browser"
    payload.screenshots_dir = tmp_path / "screenshots"
    settings = MagicMock()
    settings.is_dev = True
    settings.apply_vision_enabled = False
    settings.browser_use_max_failures = 2
    settings.browser_use_step_timeout_s = 90
    settings.browser_use_timeout_s = 180
    settings.browser_use_max_steps = 5
    settings.browser_use_wait_before_model_s = 0.01
    settings.browser_use_min_page_load_wait_s = 2
    settings.browser_use_network_idle_wait_s = 2
    settings.browser_use_wait_between_actions_s = 1
    settings.browser_use_window_width = 0
    settings.browser_use_window_height = 0
    settings.llm_provider = "ollama"
    settings.browser_use_model = ""
    settings.resolved_llm_model.return_value = "gemma4-e4b-agent:latest"
    settings.resolved_llm_base_url.return_value = "http://localhost:11434"
    settings.llm_temperature = 0.2

    class DummyProfile:
        instances: list["DummyProfile"] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            DummyProfile.instances.append(self)

    class DummyAgent:
        instances: list["DummyAgent"] = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            DummyAgent.instances.append(self)

        async def run(self, max_steps: int, on_step_start=None, on_step_end=None):
            self.max_steps = max_steps
            self.on_step_start = on_step_start
            if on_step_start is not None:
                await on_step_start(self)
            history = MagicMock()
            history.get_structured_output.return_value = None
            history.final_result.return_value = (
                '{"status":"ready_for_user_approval","current_url":"https://jobs.example/review"}'
            )
            history.urls.return_value = ["https://jobs.example/review"]
            return history

    with (
        patch("app.agents.browser_use_apply_runner.get_settings", return_value=settings),
        patch(
            "app.agents.browser_use_apply_runner._import_local_browser_use",
            return_value=(DummyAgent, DummyProfile, MagicMock()),
        ),
        patch("app.agents.browser_use_apply_runner._local_llm", return_value=MagicMock()),
    ):
        result = await _run_local_browser_use_apply(payload)

    assert result.page_url == "https://jobs.example/review"
    profile_kwargs = DummyProfile.instances[0].kwargs
    assert profile_kwargs["minimum_wait_page_load_time"] == 2
    assert profile_kwargs["wait_for_network_idle_page_load_time"] == 2
    assert profile_kwargs["wait_between_actions"] == 1
    assert profile_kwargs["no_viewport"] is True
    assert profile_kwargs["window_size"] is None
    assert "viewport" not in profile_kwargs
    kwargs = DummyAgent.instances[0].kwargs
    assert kwargs["use_vision"] is False
    assert kwargs["flash_mode"] is True
    assert kwargs["enable_planning"] is False
    assert kwargs["use_judge"] is False
    assert kwargs["use_thinking"] is False
    assert kwargs["step_timeout"] == 90
    assert kwargs["llm_timeout"] == 90
    assert kwargs["max_failures"] == 2
    assert kwargs["directly_open_url"] is False
    assert kwargs["initial_actions"] == [
        {"navigate": {"url": payload.job_url, "new_tab": False}}
    ]
    assert DummyAgent.instances[0].max_steps == 5
    assert DummyAgent.instances[0].on_step_start is not None
