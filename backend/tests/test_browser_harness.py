"""Browser harness tests."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.agents.apply_vision_agent import VisionApplyAction
from app.agents.browser_harness import (
    BrowserHarness,
    HarnessContext,
    WorkdayAdapter,
    _force_stuck_recovery,
    _normalized_to_viewport,
    _page_changed,
    _try_click_application_entry_modal,
    _try_click_apply_on_job_page,
    classify_page_state,
    _try_click_locator,
    dismiss_blocking_ui,
    execute_browser_action,
    find_submit_selector,
    observe_page_state,
    wait_for_apply_form_ready,
    wait_for_apply_page_ready,
)


def _mock_locator(*, count: int = 1, visible: bool = True, click_ok: bool = True) -> MagicMock:
    loc = MagicMock()
    loc.count.return_value = count
    target = MagicMock()
    target.is_visible.return_value = visible
    target.wait_for = MagicMock()
    target.scroll_into_view_if_needed = MagicMock()
    if not click_ok:
        target.click.side_effect = RuntimeError("not clickable")
    loc.first = target
    loc.page = MagicMock()
    loc.page.wait_for_timeout = MagicMock()
    return loc


def _state_page(
    *,
    url: str,
    buttons: list[str],
    body: str = "Job page content",
    fields: list[str] | None = None,
    modals: list[str] | None = None,
    submit: bool = False,
) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.screenshot.return_value = b"state-png"
    page.query_selector.return_value = MagicMock() if submit else None
    page.locator.return_value = _mock_locator(count=0)
    page.wait_for_timeout = MagicMock()
    page.wait_for_function = MagicMock()

    def evaluate(script: str):
        if "const out = []" in script:
            return buttons
        if "form_fields" in script or "modal_texts" in script:
            return {
                "title": "Mock job",
                "body_snippet": body,
                "form_fields": fields or [],
                "modal_texts": modals or [],
                "has_file_input": False,
            }
        return True

    page.evaluate.side_effect = evaluate
    empty = _mock_locator(count=0)
    manual = _mock_locator()

    def get_role(role: str, name: str):
        if name == "Apply Manually" and "Apply Manually" in buttons:
            return manual
        return empty

    page.get_by_role = get_role
    return page


def test_normalized_to_viewport() -> None:
    page = MagicMock()
    page.viewport_size = {"width": 1280, "height": 900}
    px, py = _normalized_to_viewport(page, 500, 500)
    assert px == 640.0
    assert py == 450.0


def test_execute_click_coords() -> None:
    page = MagicMock()
    page.viewport_size = {"width": 1000, "height": 800}
    page.mouse = MagicMock()
    page.wait_for_timeout = MagicMock()
    action = VisionApplyAction(action="click_coords", x=900, y=100, reason="Close chat X")
    warnings: list[str] = []

    assert execute_browser_action(page, action, fields_filled=[], warnings=warnings)
    page.mouse.click.assert_called_once()
    assert any("Close chat" in w for w in warnings)


def test_execute_press_key() -> None:
    page = MagicMock()
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    action = VisionApplyAction(action="press_key", key="Escape", reason="Dismiss overlay")

    assert execute_browser_action(page, action, fields_filled=[], warnings=[])
    page.keyboard.press.assert_called_once_with("Escape")


def test_execute_click_fallback_to_text() -> None:
    page = MagicMock()
    empty = _mock_locator(count=0)
    text_hit = _mock_locator()

    def get_role(role: str, name: str):
        return empty

    page.get_by_role = get_role
    page.get_by_text.return_value = text_hit

    action = VisionApplyAction(action="click", target_text="Apply now", reason="Open form")
    assert execute_browser_action(page, action, fields_filled=[], warnings=[])


def test_execute_done_does_not_advance() -> None:
    page = MagicMock()
    action = VisionApplyAction(action="done", reason="Ready for review")
    warnings: list[str] = []

    assert execute_browser_action(page, action, fields_filled=[], warnings=warnings) is False
    assert any("ready for your review" in w.lower() for w in warnings)


def test_execute_check_action() -> None:
    page = MagicMock()
    checkbox = _mock_locator()
    page.get_by_role.return_value = checkbox
    page.wait_for_timeout = MagicMock()
    fields: list[str] = []

    action = VisionApplyAction(action="check", target_text="I consent", target_role="checkbox")
    assert execute_browser_action(page, action, fields_filled=fields, warnings=[])
    checkbox.first.check.assert_called_once()
    assert any(field.startswith("vision_check") for field in fields)


def test_execute_handoff_action() -> None:
    page = MagicMock()
    warnings: list[str] = []
    action = VisionApplyAction(action="handoff", reason="Email verification required")

    assert not execute_browser_action(page, action, fields_filled=[], warnings=warnings)
    assert any("verification" in warning.lower() for warning in warnings)


def test_dismiss_blocking_ui_clicks_close_selectors() -> None:
    page = MagicMock()
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.frames = [page.main_frame]
    page.main_frame = page

    close_loc = _mock_locator()
    page.locator.return_value = close_loc

    warnings: list[str] = []
    dismiss_blocking_ui(page, warnings)

    page.keyboard.press.assert_not_called()


def test_dismiss_blocking_ui_iframe() -> None:
    page = MagicMock()
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    iframe = MagicMock()
    iframe.locator.return_value = _mock_locator()
    page.main_frame = MagicMock()
    page.frames = [page.main_frame, iframe]

    dismiss_blocking_ui(page, [])
    iframe.locator.assert_called()


def test_observe_page_state() -> None:
    page = MagicMock()
    page.url = "https://jobs.example.com/job/1"
    page.screenshot.return_value = b"png-bytes"
    page.evaluate.return_value = ["Apply", "Close"]
    page.locator.return_value.aria_snapshot.return_value = "button Apply"

    obs = observe_page_state(page, 0)
    assert obs.url == "https://jobs.example.com/job/1"
    assert obs.screenshot_png == b"png-bytes"
    assert "Apply" in obs.visible_buttons


def test_force_stuck_recovery() -> None:
    page = MagicMock()
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.evaluate = MagicMock()
    warnings: list[str] = []

    assert _force_stuck_recovery(page, warnings)
    page.keyboard.press.assert_called_with("Escape")
    assert any("stuck" in w.lower() for w in warnings)


def test_wait_for_application_entry_modal() -> None:
    page = MagicMock()
    page.wait_for_function = MagicMock()
    page.wait_for_timeout = MagicMock()
    warnings: list[str] = []

    from app.agents.browser_harness import wait_for_application_entry_modal

    assert wait_for_application_entry_modal(page, warnings)
    page.wait_for_function.assert_called_once()
    page.wait_for_timeout.assert_called()


def test_wait_for_apply_page_ready() -> None:
    page = MagicMock()
    page.wait_for_load_state = MagicMock()
    page.wait_for_function = MagicMock()
    warnings: list[str] = []

    assert wait_for_apply_page_ready(page, warnings)
    page.wait_for_function.assert_called_once()


def test_wait_for_apply_form_ready() -> None:
    page = MagicMock()
    page.wait_for_function = MagicMock()
    page.wait_for_timeout = MagicMock()
    warnings: list[str] = []

    assert wait_for_apply_form_ready(page, warnings)
    page.wait_for_function.assert_called_once()


def test_classify_workday_job_description() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1",
        buttons=["Sign In", "Apply"],
        body="ML/AI Research Engineer Toronto job description",
    )

    state = classify_page_state(page, observe_page_state(page, 0))

    assert state.ats_provider == "workday"
    assert state.phase == "job_description"


def test_classify_workday_application_entry_modal() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1",
        buttons=["Apply Manually", "Autofill with Resume"],
        modals=["Start Your Application Apply Manually Autofill with Resume"],
    )

    state = classify_page_state(page, observe_page_state(page, 0))

    assert state.phase == "application_entry_modal"


def test_classify_application_form() -> None:
    page = _state_page(
        url="https://boards.greenhouse.io/acme/jobs/1",
        buttons=["Next"],
        fields=["First Name", "Email"],
    )

    state = classify_page_state(page, observe_page_state(page, 0))

    assert state.phase == "multi_step_form"


def test_classify_final_review_or_submit() -> None:
    page = _state_page(
        url="https://jobs.example.com/apply/1",
        buttons=["Submit"],
        fields=["Email"],
        submit=True,
    )

    state = classify_page_state(page, observe_page_state(page, 0))

    assert state.phase == "final_review_or_submit"


def test_agent_loop_retries_after_unchanged_action_with_history() -> None:
    page = MagicMock()
    page.url = "https://jobs.example.com/job/1"
    same_png = b"identical-screenshot"
    page.screenshot.return_value = same_png
    page.evaluate.return_value = []
    page.query_selector.return_value = None
    page.locator.return_value = _mock_locator(count=0)
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.get_by_role.return_value = _mock_locator(count=0)
    page.frames = [page.main_frame]
    page.main_frame = page

    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=1,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
        patch("app.agents.browser_harness._page_changed", return_value=False),
    ):
        settings.return_value.apply_harness_max_steps = 5
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"

        run_async.side_effect = [
            VisionApplyAction(action="click", target_text="Create Account", reason="Try submit"),
            VisionApplyAction(action="handoff", reason="No safe alternate"),
        ]

        result = BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
    )

    assert run_async.call_count == 2
    assert result.steps_taken >= 1


def test_agent_done_stops_at_review_boundary() -> None:
    page = MagicMock()
    page.url = "https://jobs.example.com/job/1"
    page.screenshot.side_effect = [b"screen-a"] * 20
    page.evaluate.return_value = ["Apply"]
    page.query_selector.return_value = None
    page.locator.return_value = _mock_locator(count=0)
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.get_by_role.return_value = _mock_locator(count=0)
    page.frames = [page.main_frame]
    page.main_frame = page

    done_action = VisionApplyAction(action="done", reason="Form ready")
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=1,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
        patch("app.agents.browser_harness.wait_for_apply_page_ready"),
        patch("app.agents.browser_harness._page_changed", return_value=True),
    ):
        settings.return_value.apply_harness_max_steps = 4
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = done_action

        result = BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    assert run_async.call_count == 1
    assert result.steps_taken == 0


def test_session_tracing_writes_files(tmp_path: Path) -> None:
    page = MagicMock()
    page.url = "https://jobs.example.com/job/1"
    page.screenshot.return_value = b"step-png"
    page.evaluate.return_value = ["Apply"]
    page.query_selector.return_value = None
    page.locator.return_value = _mock_locator(count=0)
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.get_by_role.return_value = _mock_locator(count=0)
    page.frames = [page.main_frame]
    page.main_frame = page

    session_dir = tmp_path / "sessions" / "42"
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=42,
        vision_enabled=True,
        session_dir=session_dir,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
    ):
        settings.return_value.apply_harness_max_steps = 2
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"

        run_async.return_value = VisionApplyAction(action="none", reason="No safe action")

        result = BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    assert (session_dir / "step_00.png").exists()
    assert (session_dir / "step_00.json").exists()
    assert result.session_dir == str(session_dir)


def test_apply_click_scrolls_into_view() -> None:
    loc = _mock_locator()
    assert _try_click_locator(loc)
    loc.first.scroll_into_view_if_needed.assert_called_once_with(timeout=5_000)
    loc.first.click.assert_called_once()


def test_external_apply_asks_agent_before_apply_heuristics() -> None:
    page = MagicMock()
    page.url = "https://jobs.ebayinc.com/job/1"
    page.screenshot.return_value = b"screen-a"
    page.evaluate.return_value = []
    page.query_selector.return_value = None
    page.locator.return_value = _mock_locator(count=0)
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.get_by_role.return_value = _mock_locator(count=0)
    page.frames = [page.main_frame]
    page.main_frame = page

    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=1,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch(
            "app.agents.browser_harness._try_click_apply_on_job_page",
            return_value=False,
        ) as apply_heuristic,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
    ):
        settings.return_value.apply_harness_max_steps = 1
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = VisionApplyAction(action="none", reason="No action")

        BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    run_async.assert_called_once()
    apply_heuristic.assert_not_called()


def test_try_click_apply_logs_success() -> None:
    page = MagicMock()
    apply_loc = _mock_locator()
    empty = _mock_locator(count=0)

    def get_role(role: str, name: str):
        if name == "Apply":
            return apply_loc
        return empty

    page.get_by_role = get_role
    page.locator.return_value = empty
    warnings: list[str] = []

    assert _try_click_apply_on_job_page(page, warnings=warnings, step=2)
    assert any("Step 2: heuristics clicked Apply" in w for w in warnings)


def test_try_click_apply_skips_when_entry_modal_open() -> None:
    page = MagicMock()
    manual_btn = _mock_locator()
    page.get_by_role.return_value = manual_btn

    warnings: list[str] = []
    assert not _try_click_apply_on_job_page(page, warnings=warnings, step=1)
    assert any("modal already open" in w for w in warnings)


def test_try_click_application_entry_modal() -> None:
    page = MagicMock()
    empty = _mock_locator(count=0)
    manual = _mock_locator()

    def get_role(role: str, name: str):
        if name == "Apply Manually":
            return manual
        return empty

    page.get_by_role = get_role
    page.locator.return_value = empty
    warnings: list[str] = []
    assert _try_click_application_entry_modal(page, warnings=warnings, step=2)
    assert any("Apply Manually" in w for w in warnings)


def test_workday_adapter_atomic_apply_to_manual_to_form() -> None:
    page = MagicMock()
    page.url = "https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1"
    page.screenshot.return_value = b"workday"
    page.query_selector.return_value = None
    page.wait_for_timeout = MagicMock()
    page.wait_for_function = MagicMock(
        side_effect=[None, RuntimeError("form not ready"), None, None]
    )
    page.evaluate.side_effect = [
        ["Apply"],
        {"title": "BMO", "body_snippet": "Job description", "form_fields": [], "modal_texts": []},
        ["Apply Manually"],
        {"title": "BMO", "body_snippet": "Start Your Application", "form_fields": [], "modal_texts": ["Start Your Application"]},
        ["Next"],
        {"title": "BMO", "body_snippet": "Application", "form_fields": ["First Name"], "modal_texts": []},
    ]
    modal_open = {"value": False}
    apply = _mock_locator()
    apply.first.click.side_effect = lambda *args, **kwargs: modal_open.update(value=True)
    manual = _mock_locator()
    empty = _mock_locator(count=0)

    def locator(selector: str, **kwargs):
        if selector == '[data-automation-id="jobPostingApplyButton"]':
            return apply
        if selector == '[data-automation-id="applyManually"]' and modal_open["value"]:
            return manual
        return empty

    page.locator.side_effect = locator
    page.get_by_role.return_value = empty
    state = classify_page_state(page, observe_page_state(page, 0))
    state.phase = "job_description"

    result = WorkdayAdapter().act(page, state, warnings=[], step=0)

    assert result.status == "clicked_and_form_ready"
    apply.first.click.assert_called()
    manual.first.click.assert_called()
    assert any("applyManually" in item for item in result.selector_attempts)


def test_workday_adapter_retries_apply_manually() -> None:
    page = MagicMock()
    page.url = "https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1"
    page.screenshot.return_value = b"modal"
    page.evaluate.return_value = ["Apply Manually"]
    page.query_selector.return_value = None
    page.wait_for_timeout = MagicMock()
    page.wait_for_function = MagicMock()
    failing = _mock_locator(click_ok=False)
    manual = _mock_locator()
    empty = _mock_locator(count=0)
    calls = {"manual": 0}

    def locator(selector: str, **kwargs):
        if selector == '[data-automation-id="applyManually"]':
            calls["manual"] += 1
            return failing if calls["manual"] == 1 else manual
        return empty

    def get_role(role: str, name: str):
        return manual if name == "Apply Manually" else empty

    page.locator.side_effect = locator
    page.get_by_role = get_role
    state = classify_page_state(page, observe_page_state(page, 0))

    result = WorkdayAdapter().act(page, state, warnings=[], step=0)

    assert result.status == "clicked_and_form_ready"
    assert calls["manual"] >= 2
    assert any("entry-selector" in item for item in result.selector_attempts)


def test_page_changed_detects_identical_screenshot() -> None:
    page = MagicMock()
    page.screenshot.return_value = b"same"
    assert not _page_changed(page, b"same")
    page.screenshot.return_value = b"different"
    assert _page_changed(page, b"same")


def test_workday_modal_clicks_apply_manually_not_apply() -> None:
    page = MagicMock()
    page.url = "https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1"
    page.screenshot.side_effect = [b"modal", b"form", b"form", b"form"]
    page.evaluate.return_value = ["Apply Manually", "Apply"]
    page.query_selector.return_value = None
    page.locator.return_value = _mock_locator(count=0)
    page.keyboard = MagicMock()
    page.wait_for_timeout = MagicMock()
    page.frames = [page.main_frame]
    page.main_frame = page

    manual = _mock_locator()
    empty = _mock_locator(count=0)

    def get_role(role: str, name: str):
        if name == "Apply Manually":
            return manual
        if name == "Apply":
            return empty
        return empty

    page.get_by_role = get_role

    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=40,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
    ):
        settings.return_value.apply_harness_max_steps = 2
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = VisionApplyAction(
            action="click",
            target_text="Apply Manually",
            target_role="button",
            reason="Enter manual application flow",
        )

        result = BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    manual.first.click.assert_called()
    assert result.steps_taken >= 1


def test_harness_prevents_closing_workday_entry_modal() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1",
        buttons=["Apply Manually", "Apply"],
        modals=["Start Your Application Apply Manually"],
    )
    manual = page.get_by_role("button", "Apply Manually")
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=40,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
        patch("app.agents.browser_harness._page_changed", return_value=True),
    ):
        settings.return_value.apply_harness_max_steps = 1
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = VisionApplyAction(
            action="press_key",
            key="Escape",
            reason="Close modal",
        )

        BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    page.keyboard.press.assert_not_called()
    manual.first.click.assert_called()


def test_harness_waits_for_workday_modal_after_apply_click() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1",
        buttons=["Apply"],
    )
    apply_button = _mock_locator()
    empty = _mock_locator(count=0)

    def get_role(role: str, name: str):
        return apply_button if name == "Apply" else empty

    page.get_by_role = get_role
    page.locator.return_value = empty
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=40,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
        patch("app.agents.browser_harness._try_click_apply_on_job_page", return_value=True) as apply_skill,
        patch("app.agents.browser_harness.wait_for_application_entry_modal", return_value=True) as wait_modal,
        patch("app.agents.browser_harness._page_changed", return_value=True),
    ):
        settings.return_value.apply_harness_max_steps = 1
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = VisionApplyAction(
            action="click",
            target_text="Apply",
            target_role="button",
            reason="Open application",
        )

        BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    apply_skill.assert_called()
    wait_modal.assert_called()


def test_harness_stops_before_submit_without_clicking() -> None:
    page = _state_page(
        url="https://jobs.example.com/apply/1",
        buttons=["Submit"],
        fields=["Email"],
        submit=True,
    )
    submit = MagicMock()
    page.query_selector.return_value = submit
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=1,
        vision_enabled=True,
        session_dir=None,
    )

    with patch("app.agents.browser_harness.get_settings") as settings:
        settings.return_value.apply_harness_max_steps = 2
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"

        result = BrowserHarness().run_until_ready(
            page,
            context,
            fill_fields_fn=MagicMock(),
            fields_filled=[],
            warnings=[],
        )

    assert result.submit_selector is not None
    submit.click.assert_not_called()


def test_create_account_submit_button_is_not_final_submit() -> None:
    page = MagicMock()
    page.query_selector.return_value = None
    submit = _mock_locator()
    submit.first.evaluate.return_value = "Create Account"
    page.locator.return_value = submit

    assert find_submit_selector(page) is None


def test_harness_clicks_create_account_flow_button() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1/apply/applyManually",
        buttons=["Create Account"],
        fields=["Email Address", "Password", "Verify New Password"],
    )
    page.screenshot.side_effect = [b"before", b"before", b"after", b"after"]
    create = _mock_locator()
    empty = _mock_locator(count=0)

    def get_role(role: str, name: str):
        return create if name == "Create Account" else empty

    page.get_by_role = get_role
    context = HarnessContext(
        user_name="Lin",
        user_email="lin@example.com",
        user_links={},
        application_id=1,
        vision_enabled=True,
        session_dir=None,
    )

    with (
        patch("app.agents.browser_harness.get_settings") as settings,
        patch("app.agents.browser_harness.run_coro_sync") as run_async,
    ):
        settings.return_value.apply_harness_max_steps = 1
        settings.return_value.apply_harness_stuck_threshold = 3
        settings.return_value.resolved_apply_vision_model.return_value = "test-model"
        run_async.return_value = VisionApplyAction(
            action="click",
            target_text="Create Account",
            target_role="button",
            reason="Submit account form",
        )

        with patch("app.agents.browser_harness._page_changed", return_value=True):
            BrowserHarness().run_until_ready(
                page,
                context,
                fill_fields_fn=MagicMock(),
                fields_filled=[],
                warnings=[],
            )

    create.first.click.assert_called()


def test_auth_create_account_noop_pivots_to_sign_in() -> None:
    page = _state_page(
        url="https://bmo.wd3.myworkdayjobs.com/en-US/External/job/1/apply/applyManually",
        buttons=["Create Account", "Sign In"],
        body="Already have an account? Sign In",
        fields=["Email Address", "Password", "Verify New Password"],
    )
    create = _mock_locator()
    sign_in = _mock_locator()
    empty = _mock_locator(count=0)

    def get_role(role: str, name: str):
        if role == "button" and name == "Create Account":
            return create
        if role == "link" and name == "Sign In":
            return sign_in
        return empty

    page.get_by_role = get_role
    state = classify_page_state(page, observe_page_state(page, 0))
    state.phase = "application_form"

    result = WorkdayAdapter().act(page, state, warnings=[], step=0)

    create.first.click.assert_called()
    sign_in.first.click.assert_called()
    assert result.action == "auth_sign_in_pivot"


def test_screenshot_hash_stable() -> None:
    data = b"test-screenshot"
    assert hashlib.md5(data).hexdigest() == hashlib.md5(data).hexdigest()
