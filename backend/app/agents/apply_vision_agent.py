"""Vision-guided apply actions: multimodal LLM reads screenshots and picks next steps."""

from __future__ import annotations

import base64
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from ..config import get_settings
from ..llm.base import ChatMessage, LLMError, strip_model_artifacts
from ..llm.factory import get_llm_provider

logger = logging.getLogger(__name__)

VisionActionName = Literal[
    "click",
    "scroll",
    "fill",
    "check",
    "select",
    "press_key",
    "click_coords",
    "done",
    "handoff",
    "none",
]

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")

_SKILL_PATH = Path(__file__).with_name("apply_playwright_skill.md")

_SYSTEM = """You are the Playwright apply operator for JobBee.
Your only goal is to operate the browser with Playwright-style skills until this job application reaches the review/final-submit boundary.
You read screenshots plus DOM/accessibility summaries and choose exactly one safe next browser action.
Return ONLY JSON with:
- action: click | scroll | fill | check | select | press_key | click_coords | done | handoff | none
- target_text: exact visible label/text to click, check, select, or fill
- target_role: button | link | textbox | checkbox | radio | option (optional)
- fill_value: text to enter when action is fill
- option_text: visible option text when action is select
- key: keyboard key when action is press_key, e.g. Escape
- x, y: normalized viewport coordinates 0-1000 when action is click_coords
- reason: one short sentence

Priority order:
1) Dismiss cookie banners and close chat/support overlays blocking the page.
2) On a job description page, click Apply / Apply now / Easy Apply to open the form.
3) On a Start Your Application modal, click Apply Manually to open the form.
4) On Create Account / Sign In pages:
   - prefer creating an account when email, password, verify password, consent, and Create Account are present
   - check required consent / terms / privacy checkboxes
   - if Create Account does not advance or the page says account already exists, click Sign In
   - on Sign In pages, use the visible Sign In / Log In button after fields are filled
   - never fill honeypot fields mentioning robots, bots, website, hidden, or "do not enter"
5) Scroll down if the primary action is below the fold.
6) Fill empty required fields using the applicant data provided.
7) Click Next / Continue / Save and Continue to advance multi-step forms.
8) action=done only when the form is ready for human review or final Submit / Submit application is visible.
9) action=handoff only for CAPTCHA, email verification code, MFA, SSO, or a required question whose answer is not provided.
10) action=none only if no safe action is visible.

Never click final application Submit / Submit application.
If the previous action did not change the page, do not repeat it blindly. Try a different safe path such as checking consent, scrolling, pressing Enter, clicking the same visible control by coordinates, or switching to Sign In when an account already exists.
Use target_text for accessible Playwright selectors when possible; use click_coords only for visual-only controls or when text selectors are likely failing.
Do not treat sidebar widgets such as Save Job, Get Started, or job alerts as the primary Apply action.
For icon-only close buttons with no readable label, use click_coords.
"""


def _load_apply_skill() -> str:
    try:
        return _SKILL_PATH.read_text(encoding="utf-8")
    except Exception:
        logger.debug("Could not read apply Playwright skill", exc_info=True)
        return ""


def _system_prompt() -> str:
    skill = _load_apply_skill()
    if not skill:
        return _SYSTEM
    return f"{_SYSTEM}\n\nUse this Playwright skill as binding operating instructions:\n\n{skill}"


@dataclass(slots=True)
class VisionApplyAction:
    action: VisionActionName = "none"
    target_text: str | None = None
    target_role: str = "button"
    fill_value: str | None = None
    option_text: str | None = None
    key: str | None = None
    x: int | None = None
    y: int | None = None
    reason: str = ""


def _parse_vision_json(raw: str) -> VisionApplyAction | None:
    text = strip_model_artifacts(raw).strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK_RE.search(text)
        if not match:
            return None
        try:
            obj = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(obj, dict):
        return None

    action = str(obj.get("action", "none")).lower()
    valid = (
        "click",
        "scroll",
        "fill",
        "check",
        "select",
        "press_key",
        "click_coords",
        "done",
        "handoff",
        "none",
    )
    if action not in valid:
        action = "none"

    x_val = obj.get("x")
    y_val = obj.get("y")
    x = int(x_val) if x_val is not None else None
    y = int(y_val) if y_val is not None else None

    return VisionApplyAction(
        action=action,  # type: ignore[arg-type]
        target_text=(str(obj["target_text"]).strip() if obj.get("target_text") else None),
        target_role=str(obj.get("target_role") or "button").lower(),
        fill_value=(str(obj["fill_value"]).strip() if obj.get("fill_value") else None),
        option_text=(str(obj["option_text"]).strip() if obj.get("option_text") else None),
        key=(str(obj["key"]).strip() if obj.get("key") else None),
        x=x,
        y=y,
        reason=str(obj.get("reason") or "")[:300],
    )


def _vision_user_content(
    *,
    page_url: str,
    screenshot_png: bytes,
    user_name: str,
    user_email: str | None,
    user_links: dict[str, str],
    a11y_snapshot: str = "",
    visible_buttons: list[str] | None = None,
    page_state: str = "",
    form_fields: list[str] | None = None,
    modal_texts: list[str] | None = None,
    filled_fields: list[str] | None = None,
    action_history: list[str] | None = None,
) -> list[dict[str, Any]]:
    b64 = base64.standard_b64encode(screenshot_png).decode("ascii")
    links = ", ".join(f"{k}: {v}" for k, v in user_links.items()) or "none"
    buttons = ", ".join(visible_buttons or []) or "none detected"
    fields = ", ".join(form_fields or []) or "none detected"
    modals = " | ".join(modal_texts or []) or "none detected"
    filled = ", ".join(filled_fields or []) or "none yet"
    history = "\n".join(action_history or []) or "none yet"
    a11y = a11y_snapshot[:1500] if a11y_snapshot else "unavailable"
    text = (
        f"Page URL: {page_url}\n"
        f"Applicant name: {user_name}\n"
        f"Applicant email: {user_email or 'unknown'}\n"
        f"Applicant links: {links}\n"
        f"Detected page state: {page_state or 'unknown'}\n"
        f"Visible buttons/links: {buttons}\n"
        f"Visible form fields: {fields}\n"
        f"Visible modal/dialog text: {modals}\n"
        f"Fields already filled by JobBee: {filled}\n"
        f"Recent browser actions and outcomes:\n{history}\n"
        f"Accessibility snapshot (truncated):\n{a11y}\n"
        "What is the single best next action on this screen?"
    )
    return [
        {"type": "text", "text": text},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ]


async def decide_apply_action(
    *,
    screenshot_png: bytes,
    page_url: str,
    user_name: str,
    user_email: str | None,
    user_links: dict[str, str],
    a11y_snapshot: str = "",
    visible_buttons: list[str] | None = None,
    page_state: str = "",
    form_fields: list[str] | None = None,
    modal_texts: list[str] | None = None,
    filled_fields: list[str] | None = None,
    action_history: list[str] | None = None,
) -> VisionApplyAction:
    """Ask the vision model what to do next on the current application screen."""

    settings = get_settings()
    if not settings.apply_vision_enabled:
        return VisionApplyAction(action="none", reason="Apply vision is disabled.")

    if settings.llm_provider != "ollama" and not settings.resolved_llm_api_key():
        return VisionApplyAction(action="none", reason="No LLM API key configured for vision.")

    provider = get_llm_provider()
    try:
        resp = await provider.chat(
            [
                ChatMessage(role="system", content=_system_prompt()),
                ChatMessage(
                    role="user",
                    content=_vision_user_content(
                        page_url=page_url,
                        screenshot_png=screenshot_png,
                        user_name=user_name,
                        user_email=user_email,
                        user_links=user_links,
                        a11y_snapshot=a11y_snapshot,
                        visible_buttons=visible_buttons,
                        page_state=page_state,
                        form_fields=form_fields,
                        modal_texts=modal_texts,
                        filled_fields=filled_fields,
                        action_history=action_history,
                    ),
                ),
            ],
            model=settings.resolved_apply_vision_model(),
            temperature=0.1,
            max_tokens=300,
            response_format={"type": "json_object"},
            timeout_s=settings.llm_timeout_s,
        )
    except LLMError as e:
        logger.warning("Vision apply decision failed: %s", e)
        return VisionApplyAction(action="none", reason=str(e))

    parsed = _parse_vision_json(resp.content)
    if parsed is None:
        logger.warning("Vision apply non-JSON: %r", resp.content[:200])
        return VisionApplyAction(action="none", reason="Could not parse vision model response.")
    return parsed
