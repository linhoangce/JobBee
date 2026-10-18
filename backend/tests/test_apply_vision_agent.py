"""Apply vision agent tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.apply_vision_agent import (
    VisionApplyAction,
    _parse_vision_json,
    _system_prompt,
    decide_apply_action,
)


def test_parse_vision_json() -> None:
    raw = json.dumps(
        {
            "action": "click",
            "target_text": "Apply for this job",
            "target_role": "button",
            "reason": "Open the application form",
        }
    )
    action = _parse_vision_json(raw)
    assert action is not None
    assert action.action == "click"
    assert action.target_text == "Apply for this job"


def test_parse_vision_json_click_coords_and_press_key() -> None:
    raw = json.dumps(
        {
            "action": "click_coords",
            "x": 950,
            "y": 80,
            "reason": "Close chatbot X button",
        }
    )
    action = _parse_vision_json(raw)
    assert action is not None
    assert action.action == "click_coords"
    assert action.x == 950
    assert action.y == 80

    raw_key = json.dumps({"action": "press_key", "key": "Escape", "reason": "Dismiss modal"})
    key_action = _parse_vision_json(raw_key)
    assert key_action is not None
    assert key_action.action == "press_key"
    assert key_action.key == "Escape"


def test_parse_vision_json_check_select_and_handoff() -> None:
    check = _parse_vision_json(
        json.dumps(
            {
                "action": "check",
                "target_text": "I consent",
                "target_role": "checkbox",
                "reason": "Required consent",
            }
        )
    )
    assert check is not None
    assert check.action == "check"
    assert check.target_role == "checkbox"

    select = _parse_vision_json(
        json.dumps(
            {
                "action": "select",
                "target_text": "Country",
                "option_text": "Canada",
                "reason": "Select location",
            }
        )
    )
    assert select is not None
    assert select.action == "select"
    assert select.option_text == "Canada"

    handoff = _parse_vision_json(json.dumps({"action": "handoff", "reason": "MFA required"}))
    assert handoff is not None
    assert handoff.action == "handoff"


def test_system_prompt_loads_apply_playwright_skill() -> None:
    prompt = _system_prompt()

    assert "JobBee Apply Playwright Skill" in prompt
    assert "If a Workday `Start Your Application` modal is open" in prompt
    assert "Do not click background `Apply` while an application-entry modal is open" in prompt


@pytest.mark.asyncio
async def test_decide_apply_action_uses_vision_model() -> None:
    from app.llm.base import ChatResponse

    mock_resp = ChatResponse(
        content=json.dumps(
            {
                "action": "scroll",
                "reason": "Apply button is below the fold",
            }
        ),
        model="qwen/qwen2.5-vl-32b-instruct",
        provider="openai",
        raw={},
    )

    with patch("app.agents.apply_vision_agent.get_settings") as settings:
        settings.return_value.apply_vision_enabled = True
        settings.return_value.resolved_llm_api_key.return_value = "key"
        settings.return_value.resolved_apply_vision_model.return_value = (
            "qwen/qwen2.5-vl-32b-instruct"
        )
        settings.return_value.llm_timeout_s = 60.0
        with patch(
            "app.agents.apply_vision_agent.get_llm_provider",
        ) as provider_factory:
            provider = provider_factory.return_value
            provider.chat = AsyncMock(return_value=mock_resp)
            action = await decide_apply_action(
                screenshot_png=b"png",
                page_url="https://jobs.ebayinc.com/us/en/job/123",
                user_name="Lin",
                user_email="lin@example.com",
                user_links={},
                page_state="application_form:workday",
                form_fields=["Email", "Password"],
                modal_texts=[],
            )

    assert action.action == "scroll"
    provider.chat.assert_awaited_once()
