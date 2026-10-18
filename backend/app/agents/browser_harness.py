"""Observe-plan-act browser harness for external application sites."""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal
from urllib.parse import urlparse

from ..config import get_settings
from .apply_vision_agent import VisionApplyAction, decide_apply_action
from .playwright_runner import run_coro_sync

logger = logging.getLogger(__name__)

_MIN_DOM_MS = 5_000

PagePhase = Literal[
    "loading",
    "job_description",
    "blocking_overlay",
    "application_entry_modal",
    "login_or_signup_required",
    "application_form",
    "multi_step_form",
    "final_review_or_submit",
    "stuck_or_unknown",
]

ATSProvider = Literal["workday", "greenhouse", "lever", "ashby", "generic"]

ActionStatus = Literal[
    "clicked_and_modal_opened",
    "clicked_and_form_ready",
    "clicked_but_no_change",
    "not_found",
    "blocked",
    "done",
]

_COOKIE_DISMISS_SELECTORS = (
    'button:has-text("Accept Cookies")',
    'button:has-text("Accept")',
    'button:has-text("Accept All")',
    'button:has-text("Allow all")',
    'button:has-text("I agree")',
    'button:has-text("Agree")',
    "#onetrust-accept-btn-handler",
)

_OVERLAY_CLOSE_SELECTORS = (
    'button[aria-label="Close"]',
    'button[aria-label="Dismiss"]',
    'button:has-text("Close")',
    'button:has-text("No thanks")',
    'button[title="Close"]',
    '[data-testid*="close" i]',
    '[aria-label*="close" i]',
)

_CHAT_WIDGET_SELECTORS = (
    '[class*="chat" i] button',
    '[id*="chat" i] button',
    '[aria-label*="chat" i]',
    '[class*="widget" i] button[aria-label*="close" i]',
)

_FLOW_BUTTON_NAMES = (
    "Create Account",
    "Create account",
    "Next",
    "Continue",
    "Review",
    "Save and continue",
    "Save & Continue",
    "Save and Continue",
)

_AUTH_SUBMIT_NAMES = (
    "Create Account",
    "Create account",
    "Sign In",
    "Sign in",
    "Log In",
    "Log in",
)

_APPLY_ENTRY_NAMES = (
    "Apply for this job",
    "Apply now",
    "Apply Now",
    "Easy Apply",
    "Apply",
    "Start application",
)

# Workday / ATS "Start Your Application" modal choices (order = preference).
_APPLICATION_ENTRY_NAMES = (
    "Apply Manually",
    "Autofill with Resume",
    "Use My Last Application",
    "Apply With LinkedIn",
)

_WORKDAY_APPLY_SELECTORS = (
    '[data-automation-id="jobPostingApplyButton"]',
)

_WORKDAY_ENTRY_SELECTORS = (
    '[data-automation-id="applyManually"]',
    '[data-automation-id="applyManuallyButton"]',
)

_PAGE_READY_JS = """() => {
    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
    if (text.length > 100) return true;
    return !!document.querySelector(
        'button, [role="button"], a[role="link"], [data-automation-id="jobPostingApplyButton"]'
    );
}"""

_APPLICATION_ENTRY_READY_JS = """() => {
    const want = ['Apply Manually', 'Autofill with Resume', 'Use My Last Application'];
    for (const el of document.querySelectorAll('button, [role="button"], a[role="button"]')) {
        if (!el.offsetParent) continue;
        const t = (el.innerText || el.getAttribute('aria-label') || '').trim();
        if (!want.some((w) => t.includes(w))) continue;
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') continue;
        const r = el.getBoundingClientRect();
        if (r.width < 8 || r.height < 8) continue;
        return true;
    }
    return false;
}"""

_APPLY_FORM_READY_JS = """() => {
    const text = (document.body?.innerText || '').replace(/\\s+/g, ' ').trim();
    const entryModalOpen = /Start Your Application/i.test(text)
        && /Apply Manually|Autofill with Resume|Use My Last Application/i.test(text);
    if (entryModalOpen) return false;

    const formFields = document.querySelectorAll(
        'input:not([type="hidden"]):not([type="button"]):not([type="submit"]), textarea, select, input[type="file"]'
    );
    if (formFields.length > 0) return true;

    const flowText = ['Next', 'Continue', 'Save and Continue', 'Save & Continue', 'Review', 'Submit'];
    for (const el of document.querySelectorAll('button, [role="button"], input[type="submit"]')) {
        if (!el.offsetParent) continue;
        const t = (el.innerText || el.getAttribute('aria-label') || el.value || '').trim();
        if (flowText.some((w) => t.includes(w))) return true;
    }

    return !!document.querySelector(
        '[data-automation-id="applyFlowPage"], [data-automation-id*="application"], [data-automation-id*="questionnaire"]'
    );
}"""

_PAGE_SUMMARY_JS = """() => {
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const body = clean(document.body?.innerText || '');
    const visible = (el) => {
        const r = el.getBoundingClientRect();
        return !!(el.offsetParent || r.width || r.height);
    };
    const labels = [];
    for (const el of document.querySelectorAll(
        'input:not([type="hidden"]):not([type="button"]):not([type="submit"]), textarea, select, input[type="file"]'
    )) {
        if (!visible(el)) continue;
        let label = el.getAttribute('aria-label') || el.getAttribute('placeholder')
            || el.getAttribute('name') || el.getAttribute('id') || '';
        if (el.id) {
            const explicit = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (explicit?.innerText) label = explicit.innerText;
        }
        const parentLabel = el.closest('label');
        if (!label && parentLabel?.innerText) label = parentLabel.innerText;
        labels.push(clean(label).slice(0, 80) || el.tagName.toLowerCase());
        if (labels.length >= 30) break;
    }
    const dialogs = [];
    for (const el of document.querySelectorAll('[role="dialog"], dialog, [aria-modal="true"]')) {
        if (!visible(el)) continue;
        const text = clean(el.innerText || el.getAttribute('aria-label') || '');
        if (text) dialogs.push(text.slice(0, 300));
        if (dialogs.length >= 5) break;
    }
    return {
        title: document.title || '',
        body_snippet: body.slice(0, 2000),
        form_fields: labels,
        modal_texts: dialogs,
        has_file_input: !!document.querySelector('input[type="file"]'),
    };
}"""

_SUBMIT_SELECTORS = (
    'button[aria-label="Submit application"]',
    'button:has-text("Submit application")',
    'button:has-text("Send application")',
    'button:has-text("Submit")',
    'button[type="submit"]',
    'input[type="submit"]',
)


@dataclass(slots=True)
class HarnessContext:
    user_name: str
    user_email: str | None
    user_links: dict[str, str]
    application_id: int
    vision_enabled: bool
    session_dir: Path | None = None


@dataclass(slots=True)
class PageObservation:
    url: str
    screenshot_png: bytes
    a11y_snapshot: str
    visible_buttons: list[str]
    step_index: int


@dataclass(slots=True)
class PageState:
    phase: PagePhase
    ats_provider: ATSProvider
    url: str
    hostname: str
    title: str = ""
    body_snippet: str = ""
    visible_buttons: list[str] = field(default_factory=list)
    form_fields: list[str] = field(default_factory=list)
    modal_texts: list[str] = field(default_factory=list)
    screenshot_hash: str = ""
    step_index: int = 0


@dataclass(slots=True)
class ActionResult:
    status: ActionStatus
    reason: str
    before_state: PageState | None = None
    after_state: PageState | None = None
    action: str | None = None
    selector_attempts: list[str] = field(default_factory=list)
    vision_action: VisionApplyAction | None = None

    @property
    def advanced(self) -> bool:
        return self.status in ("clicked_and_modal_opened", "clicked_and_form_ready", "done")


@dataclass(slots=True)
class HarnessResult:
    submit_selector: str | None = None
    session_dir: str | None = None
    steps_taken: int = 0


def find_submit_selector(page) -> str | None:
    for selector in _SUBMIT_SELECTORS[:-2]:
        try:
            if page.query_selector(selector) is not None:
                return selector
        except Exception:
            continue
    for selector in ("button[type='submit']", "input[type='submit']"):
        try:
            label = page.locator(selector).first.evaluate(
                """(el) => (el.innerText || el.getAttribute('aria-label') || el.value || '').trim()"""
            )
            low = (label or "").lower()
            if "submit" in low and not any(
                skip in low for skip in ("create", "sign in", "login", "next", "continue")
            ):
                return selector
        except Exception:
            continue
    return None


def _wait_dom_settle(page, *, ms: int | None = None) -> None:
    """Pause until dynamic UI (modals, SPAs) finishes rendering."""

    try:
        page.wait_for_timeout(ms if ms is not None else _MIN_DOM_MS)
    except Exception:
        pass


def _ensure_locator_ready(locator, *, timeout_ms: int = _MIN_DOM_MS) -> bool:
    timeout_ms = max(timeout_ms, _MIN_DOM_MS)
    try:
        if locator.count() == 0:
            return False
        target = locator.first
        target.wait_for(state="attached", timeout=timeout_ms)
        target.wait_for(state="visible", timeout=timeout_ms)
        return target.is_visible()
    except Exception:
        return False


def _try_click_locator(locator, *, timeout: int = _MIN_DOM_MS) -> bool:
    timeout = max(timeout, _MIN_DOM_MS)
    try:
        if not _ensure_locator_ready(locator, timeout_ms=timeout):
            return False
        target = locator.first
        target.scroll_into_view_if_needed(timeout=timeout)
        target.click(timeout=timeout)
        _wait_dom_settle(locator.page)
        return True
    except Exception:
        return False


def _try_click_locator_with_trace(
    locator,
    *,
    label: str,
    attempts: list[str],
    timeout: int = _MIN_DOM_MS,
) -> bool:
    timeout = max(timeout, _MIN_DOM_MS)
    try:
        count = locator.count()
        attempts.append(f"{label}: count={count}")
        if count == 0:
            return False
        target = locator.first
        target.wait_for(state="attached", timeout=timeout)
        target.wait_for(state="visible", timeout=timeout)
        if not target.is_visible():
            attempts.append(f"{label}: not visible")
            return False
        target.scroll_into_view_if_needed(timeout=timeout)
        target.click(timeout=timeout)
        _wait_dom_settle(locator.page)
        attempts.append(f"{label}: clicked")
        return True
    except Exception as e:
        attempts.append(f"{label}: failed: {type(e).__name__}: {e}")
        return False


def _try_click_text_ancestor(
    page,
    text: str,
    *,
    attempts: list[str],
    timeout: int = _MIN_DOM_MS,
) -> bool:
    try:
        handle = page.locator(
            "button, [role='button'], a, div, span",
            has_text=text,
        ).first
        return _try_click_locator_with_trace(
            handle,
            label=f"ancestor-text:{text}",
            attempts=attempts,
            timeout=timeout,
        )
    except Exception as e:
        attempts.append(f"ancestor-text:{text}: failed: {type(e).__name__}: {e}")
        return False


def _collect_visible_buttons(page) -> list[str]:
    try:
        return page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const labelText = (el) => {
                    let t = (el.innerText || el.getAttribute('aria-label')
                        || el.getAttribute('title') || el.value || '').trim();
                    const labelledBy = el.getAttribute('aria-labelledby');
                    if (labelledBy) {
                        for (const id of labelledBy.split(/\\s+/)) {
                            const node = document.getElementById(id);
                            if (node && node.innerText) {
                                t = node.innerText.trim();
                                break;
                            }
                        }
                    }
                    return t;
                };
                for (const el of document.querySelectorAll(
                    'button, a[role="button"], [role="link"], input[type="submit"]'
                )) {
                    if (!el.offsetParent && el.tagName !== 'BODY') continue;
                    const t = labelText(el);
                    if (!t || seen.has(t)) continue;
                    seen.add(t);
                    out.push(t.slice(0, 80));
                    if (out.length >= 30) break;
                }
                return out;
            }"""
        )
    except Exception:
        return []


def _collect_a11y_snapshot(page) -> str:
    try:
        body = page.locator("body")
        if hasattr(body, "aria_snapshot"):
            return str(body.aria_snapshot())[:2000]
    except Exception:
        pass
    try:
        snapshot = page.accessibility.snapshot()
        return json.dumps(snapshot, ensure_ascii=False)[:2000]
    except Exception:
        return ""


def observe_page_state(page, step_index: int) -> PageObservation:
    try:
        screenshot_png = page.screenshot(type="png", full_page=False)
    except Exception:
        screenshot_png = b""
    return PageObservation(
        url=page.url,
        screenshot_png=screenshot_png,
        a11y_snapshot=_collect_a11y_snapshot(page),
        visible_buttons=_collect_visible_buttons(page),
        step_index=step_index,
    )


def _detect_ats_provider(url: str) -> ATSProvider:
    host = urlparse(url).hostname or ""
    low = host.lower()
    if "myworkdayjobs.com" in low or ".wd" in low or "workdayjobs.com" in low:
        return "workday"
    if "greenhouse.io" in low:
        return "greenhouse"
    if "lever.co" in low:
        return "lever"
    if "ashbyhq.com" in low or "ashby" in low:
        return "ashby"
    return "generic"


def _collect_page_summary(page) -> dict:
    try:
        summary = page.evaluate(_PAGE_SUMMARY_JS)
        return summary if isinstance(summary, dict) else {}
    except Exception:
        return {}


def classify_page_state(page, observation: PageObservation | None = None) -> PageState:
    if observation is None:
        observation = observe_page_state(page, 0)

    summary = _collect_page_summary(page)
    buttons = observation.visible_buttons
    body = str(summary.get("body_snippet") or "")
    modal_texts = [str(v) for v in summary.get("modal_texts") or []]
    form_fields = [str(v) for v in summary.get("form_fields") or []]
    joined = " ".join([body, *buttons, *modal_texts]).lower()
    screenshot_hash = hashlib.md5(observation.screenshot_png).hexdigest()
    submit_selector = find_submit_selector(page)

    if not body and not buttons and not form_fields:
        phase: PagePhase = "loading"
    elif any(hint in joined for hint in ("captcha", "verify you are human", "multi-factor", "mfa")):
        phase = "login_or_signup_required"
    elif _application_entry_modal_open(page) or any(
        "apply manually" in text.lower() for text in [*buttons, *modal_texts]
    ):
        phase = "application_entry_modal"
    elif submit_selector:
        phase = "final_review_or_submit"
    elif form_fields:
        phase = "application_form"
        if any(
            label.lower() in ("next", "continue", "save and continue", "save & continue", "review")
            for label in buttons
        ):
            phase = "multi_step_form"
    elif any("apply" == label.strip().lower() or "apply now" in label.lower() for label in buttons):
        phase = "job_description"
    elif any(hint in joined for hint in ("sign in", "log in", "create account", "register", "sign up")) and not form_fields:
        phase = "login_or_signup_required"
    elif any(hint in joined for hint in ("cookie", "chat", "close")):
        phase = "blocking_overlay"
    else:
        phase = "stuck_or_unknown"

    return PageState(
        phase=phase,
        ats_provider=_detect_ats_provider(observation.url),
        url=observation.url,
        hostname=urlparse(observation.url).hostname or "",
        title=str(summary.get("title") or ""),
        body_snippet=body[:2000],
        visible_buttons=buttons,
        form_fields=form_fields,
        modal_texts=modal_texts,
        screenshot_hash=screenshot_hash,
        step_index=observation.step_index,
    )


def wait_for_apply_page_ready(
    page,
    warnings: list[str],
    *,
    timeout_ms: int = 45_000,
) -> bool:
    """Wait for SPA application pages (Workday, etc.) to render before clicking."""

    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        page.wait_for_function(_PAGE_READY_JS, timeout=timeout_ms)
        _wait_dom_settle(page)
        return True
    except Exception:
        warnings.append("Timed out waiting for the application page to finish loading.")
        return False


def wait_for_application_entry_modal(
    page,
    warnings: list[str],
    *,
    timeout_ms: int = 30_000,
) -> bool:
    """Wait for Workday/ATS application-entry modal buttons to be interactive."""

    try:
        page.wait_for_function(_APPLICATION_ENTRY_READY_JS, timeout=timeout_ms)
        _wait_dom_settle(page)
        return True
    except Exception:
        warnings.append("Timed out waiting for the application entry modal to finish loading.")
        return False


def wait_for_apply_form_ready(
    page,
    warnings: list[str],
    *,
    timeout_ms: int = 30_000,
) -> bool:
    """Wait until an ATS application form or flow page is visible."""

    try:
        page.wait_for_function(_APPLY_FORM_READY_JS, timeout=timeout_ms)
        _wait_dom_settle(page)
        return True
    except Exception:
        warnings.append("Timed out waiting for the application form to open.")
        return False


def _dismiss_on_surface(page, warnings: list[str]) -> bool:
    dismissed = False
    for selector in _COOKIE_DISMISS_SELECTORS:
        try:
            if _try_click_locator(page.locator(selector), timeout=2_000):
                warnings.append("Dismissed a cookie consent banner.")
                dismissed = True
                break
        except Exception:
            continue

    for selector in _OVERLAY_CLOSE_SELECTORS + _CHAT_WIDGET_SELECTORS:
        try:
            if _try_click_locator(page.locator(selector), timeout=2_000):
                warnings.append("Closed an overlay or chat widget.")
                dismissed = True
                break
        except Exception:
            continue
    return dismissed


def dismiss_blocking_ui(page, warnings: list[str]) -> bool:
    """Fast-path dismiss for cookies, overlays, chat widgets, and iframes."""

    dismissed = _dismiss_on_surface(page, warnings)

    try:
        frames = page.frames
        for frame in frames:
            if frame == page.main_frame:
                continue
            try:
                for selector in _OVERLAY_CLOSE_SELECTORS + _CHAT_WIDGET_SELECTORS:
                    if _try_click_locator(frame.locator(selector), timeout=1_500):
                        warnings.append("Closed overlay inside iframe.")
                        dismissed = True
                        break
            except Exception:
                continue
    except Exception:
        pass

    return dismissed


def _normalized_to_viewport(page, x: int, y: int) -> tuple[float, float]:
    try:
        size = page.viewport_size or {"width": 1280, "height": 900}
        width = size["width"]
        height = size["height"]
        px = max(0, min(width - 1, (x / 1000.0) * width))
        py = max(0, min(height - 1, (y / 1000.0) * height))
        return px, py
    except Exception:
        return float(x), float(y)


def execute_browser_action(
    page,
    action: VisionApplyAction,
    *,
    fields_filled: list[str],
    warnings: list[str],
) -> bool:
    """Run one planned action with multi-strategy fallbacks."""

    if action.reason:
        warnings.append(f"Vision: {action.reason}")

    if action.action == "done":
        warnings.append("Vision: application form looks ready for your review.")
        return False
    if action.action == "handoff":
        warnings.append(f"Vision handoff: {action.reason or 'Human input is required.'}")
        return False
    if action.action == "none":
        return False

    if action.action == "scroll":
        try:
            page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.75))")
            _wait_dom_settle(page)
            return True
        except Exception:
            return False

    if action.action == "press_key":
        key = action.key or "Escape"
        try:
            page.keyboard.press(key)
            _wait_dom_settle(page)
            return True
        except Exception:
            return False

    if action.action == "click_coords" and action.x is not None and action.y is not None:
        try:
            px, py = _normalized_to_viewport(page, action.x, action.y)
            page.mouse.click(px, py)
            _wait_dom_settle(page)
            return True
        except Exception:
            return False

    if action.action == "fill":
        value = action.fill_value
        if not value or not action.target_text:
            return False
        for strategy in (
            lambda: page.get_by_label(action.target_text, exact=False),
            lambda: page.get_by_role("textbox", name=action.target_text),
            lambda: page.get_by_placeholder(action.target_text, exact=False),
        ):
            try:
                loc = strategy()
                if loc.count() == 0:
                    continue
                loc.first.fill(value, timeout=5_000)
                fields_filled.append(f"vision:{action.target_text[:40]}")
                return True
            except Exception:
                continue
        return False

    if action.action == "check" and action.target_text:
        for strategy in (
            lambda: page.get_by_role("checkbox", name=action.target_text),
            lambda: page.get_by_label(action.target_text, exact=False),
            lambda: page.get_by_text(action.target_text, exact=False),
        ):
            try:
                loc = strategy()
                if loc.count() == 0:
                    continue
                target = loc.first
                target.scroll_into_view_if_needed(timeout=5_000)
                try:
                    target.check(timeout=5_000)
                except Exception:
                    target.click(timeout=5_000)
                _wait_dom_settle(page)
                fields_filled.append(f"vision_check:{action.target_text[:40]}")
                return True
            except Exception:
                continue
        return False

    if action.action == "select" and action.target_text:
        option = action.option_text or action.fill_value
        if not option:
            return False
        for strategy in (
            lambda: page.get_by_label(action.target_text, exact=False),
            lambda: page.locator(f'select[aria-label*="{action.target_text}" i]'),
        ):
            try:
                loc = strategy()
                if loc.count() == 0:
                    continue
                target = loc.first
                target.scroll_into_view_if_needed(timeout=5_000)
                target.select_option(label=option, timeout=5_000)
                _wait_dom_settle(page)
                fields_filled.append(f"vision_select:{action.target_text[:40]}")
                return True
            except Exception:
                continue
        return False

    if action.action == "click" and action.target_text:
        click_attempts: list[str] = []
        if action.target_text in _APPLICATION_ENTRY_NAMES:
            if _try_click_application_entry_modal(
                page,
                warnings=warnings,
                selector_attempts=click_attempts,
            ):
                return True
        if _is_apply_entry_action(action):
            if _try_click_apply_on_job_page(
                page,
                warnings=warnings,
                selector_attempts=click_attempts,
            ):
                return True
        role = action.target_role if action.target_role in ("button", "link", "textbox") else "button"
        strategies = [
            lambda: page.get_by_role(role, name=action.target_text),
            lambda: page.get_by_role("button", name=action.target_text),
            lambda: page.get_by_role("link", name=action.target_text),
            lambda: page.locator(f'button:has-text("{action.target_text}")'),
            lambda: page.locator(f'a:has-text("{action.target_text}")'),
            lambda: page.locator(f'[role="button"]:has-text("{action.target_text}")'),
            lambda: page.get_by_label(action.target_text, exact=False),
            lambda: page.get_by_text(action.target_text, exact=False),
        ]
        for strategy in strategies:
            try:
                if _try_click_locator(strategy()):
                    return True
            except Exception:
                continue
        try:
            if _try_click_text_ancestor(page, action.target_text, attempts=click_attempts):
                return True
        except Exception:
            pass
        if action.x is not None and action.y is not None:
            try:
                px, py = _normalized_to_viewport(page, action.x, action.y)
                page.mouse.click(px, py)
                _wait_dom_settle(page)
                return True
            except Exception:
                pass

    return False


def _action_would_close_application_modal(action: VisionApplyAction) -> bool:
    target = (action.target_text or "").strip().lower()
    key = (action.key or "").strip().lower()
    reason = (action.reason or "").strip().lower()
    return (
        (action.action == "press_key" and key == "escape")
        or (
            action.action == "click"
            and any(word in target for word in ("close", "dismiss", "cancel"))
        )
        or (
            action.action == "click_coords"
            and any(word in reason for word in ("close", "dismiss", "cancel"))
        )
    )


def _click_flow_button(page, name: str) -> bool:
    try:
        btn = page.get_by_role("button", name=name)
        if not _try_click_locator(btn):
            return False
        return True
    except Exception:
        return False


def _looks_like_auth_form(state: PageState) -> bool:
    labels = " ".join(state.form_fields).lower()
    text = f"{state.title} {state.body_snippet} {' '.join(state.visible_buttons)}".lower()
    return "password" in labels and (
        "email" in labels
        or "create account" in text
        or "sign in" in text
        or "log in" in text
    )


def _should_try_sign_in_after_signup(state: PageState, after: PageState) -> bool:
    text = f"{state.body_snippet} {after.body_snippet} {' '.join(after.visible_buttons)}".lower()
    return (
        "already have an account" in text
        or "sign in" in text
        or "account already" in text
        or "email address already" in text
        or "already exists" in text
    )


def _try_click_auth_sign_in(page, attempts: list[str]) -> bool:
    for role in ("link", "button"):
        for name in ("Sign In", "Sign in", "Log In", "Log in"):
            if _try_click_locator_with_trace(
                page.get_by_role(role, name=name),
                label=f"auth-signin-role:{role}:{name}",
                attempts=attempts,
            ):
                return True
    for selector in (
        'a:has-text("Sign In")',
        'button:has-text("Sign In")',
        '[role="link"]:has-text("Sign In")',
        '[role="button"]:has-text("Sign In")',
        'a:has-text("Log In")',
        'button:has-text("Log In")',
    ):
        if _try_click_locator_with_trace(
            page.locator(selector),
            label=f"auth-signin-selector:{selector}",
            attempts=attempts,
        ):
            return True
    return _try_click_text_ancestor(page, "Sign In", attempts=attempts)


def _application_entry_modal_open(page) -> bool:
    for selector in _WORKDAY_ENTRY_SELECTORS:
        try:
            loc = page.locator(selector)
            if loc.count() > 0 and loc.first.is_visible():
                return True
        except Exception:
            continue
    for name in _APPLICATION_ENTRY_NAMES:
        for role in ("button", "link"):
            try:
                loc = page.get_by_role(role, name=name)
                if loc.count() > 0 and loc.first.is_visible():
                    return True
            except Exception:
                continue
    return False


def _page_changed(page, before_png: bytes) -> bool:
    try:
        after = page.screenshot(type="png", full_page=False)
        return hashlib.md5(after).hexdigest() != hashlib.md5(before_png).hexdigest()
    except Exception:
        return True


def _try_click_application_entry_modal(
    page,
    *,
    warnings: list[str] | None = None,
    step: int = 0,
    selector_attempts: list[str] | None = None,
) -> bool:
    attempts = selector_attempts if selector_attempts is not None else []
    for selector in _WORKDAY_ENTRY_SELECTORS:
        if _try_click_locator_with_trace(
            page.locator(selector),
            label=f"entry-selector:{selector}",
            attempts=attempts,
        ):
            if warnings is not None:
                warnings.append(
                    f"Step {step}: heuristics clicked application entry ({selector})."
                )
            return True
    for name in _APPLICATION_ENTRY_NAMES:
        for role in ("button", "link"):
            if _try_click_locator_with_trace(
                page.get_by_role(role, name=name),
                label=f"entry-role:{role}:{name}",
                attempts=attempts,
            ):
                if warnings is not None:
                    warnings.append(
                        f"Step {step}: heuristics clicked application entry '{name}' ({role})."
                    )
                return True
        if _try_click_locator_with_trace(
            page.locator(f'button:has-text("{name}")'),
            label=f"entry-button-text:{name}",
            attempts=attempts,
        ):
            if warnings is not None:
                warnings.append(
                    f"Step {step}: heuristics clicked application entry '{name}' (has-text)."
                )
            return True
        if _try_click_text_ancestor(page, name, attempts=attempts):
            if warnings is not None:
                warnings.append(
                    f"Step {step}: heuristics clicked application entry '{name}' (ancestor)."
                )
            return True
    if warnings is not None:
        warnings.append(f"Step {step}: application entry modal open but no entry button clicked.")
    return False


def _try_click_apply_on_job_page(
    page,
    *,
    warnings: list[str] | None = None,
    step: int = 0,
    selector_attempts: list[str] | None = None,
) -> bool:
    attempts = selector_attempts if selector_attempts is not None else []
    if _application_entry_modal_open(page):
        if warnings is not None:
            warnings.append(f"Step {step}: application entry modal already open — skipping Apply.")
        return False

    for selector in _WORKDAY_APPLY_SELECTORS:
        if _try_click_locator_with_trace(
            page.locator(selector),
            label=f"apply-selector:{selector}",
            attempts=attempts,
        ):
            if warnings is not None:
                warnings.append(f"Step {step}: heuristics clicked Workday Apply ({selector}).")
            wait_for_application_entry_modal(page, warnings or [])
            return True

    for name in _APPLY_ENTRY_NAMES:
        for role in ("button", "link"):
            if _try_click_locator_with_trace(
                page.get_by_role(role, name=name),
                label=f"apply-role:{role}:{name}",
                attempts=attempts,
            ):
                if warnings is not None:
                    warnings.append(
                        f"Step {step}: heuristics clicked Apply '{name}' ({role})."
                    )
                wait_for_application_entry_modal(page, warnings or [])
                return True
        if _try_click_locator_with_trace(
            page.locator(f'button:has-text("{name}")'),
            label=f"apply-button-text:{name}",
            attempts=attempts,
        ):
            if warnings is not None:
                warnings.append(
                    f"Step {step}: heuristics clicked Apply '{name}' (has-text)."
                )
            wait_for_application_entry_modal(page, warnings or [])
            return True
        if _try_click_text_ancestor(page, name, attempts=attempts):
            if warnings is not None:
                warnings.append(f"Step {step}: heuristics clicked Apply '{name}' (ancestor).")
            wait_for_application_entry_modal(page, warnings or [])
            return True
    if warnings is not None:
        warnings.append(f"Step {step}: heuristics — no Apply button found.")
    return False


class WorkdayAdapter:
    """Deterministic Workday flow from job posting into the application form."""

    provider: ATSProvider = "workday"

    def act(
        self,
        page,
        state: PageState,
        *,
        warnings: list[str],
        step: int,
    ) -> ActionResult:
        attempts: list[str] = []
        before = state

        if state.phase in ("application_form", "multi_step_form", "blocking_overlay", "stuck_or_unknown"):
            return GenericAdapter().act(page, state, warnings=warnings, step=step)

        if state.phase == "application_entry_modal":
            return self._click_entry(page, before, warnings=warnings, step=step, attempts=attempts)

        if state.phase != "job_description":
            return ActionResult(
                status="not_found",
                reason=f"Workday adapter has no action for {state.phase}.",
                before_state=before,
                selector_attempts=attempts,
            )

        if not _try_click_apply_on_job_page(
            page,
            warnings=warnings,
            step=step,
            selector_attempts=attempts,
        ):
            return ActionResult(
                status="not_found",
                reason="Workday Apply button was not found or could not be clicked.",
                before_state=before,
                after_state=classify_page_state(page, observe_page_state(page, step)),
                action="workday_apply",
                selector_attempts=attempts,
            )

        after_apply = classify_page_state(page, observe_page_state(page, step))
        if wait_for_apply_form_ready(page, warnings, timeout_ms=8_000):
            after = classify_page_state(page, observe_page_state(page, step))
            return ActionResult(
                status="clicked_and_form_ready",
                reason="Workday Apply opened the application form directly.",
                before_state=before,
                after_state=after,
                action="workday_apply",
                selector_attempts=attempts,
            )

        if wait_for_application_entry_modal(page, warnings, timeout_ms=20_000) or _application_entry_modal_open(page):
            warnings.append(f"Step {step}: Workday application entry modal opened.")
            return self._click_entry(page, after_apply, warnings=warnings, step=step, attempts=attempts)

        return ActionResult(
            status="clicked_but_no_change",
            reason="Clicked Workday Apply but neither modal nor form became ready.",
            before_state=before,
            after_state=classify_page_state(page, observe_page_state(page, step)),
            action="workday_apply",
            selector_attempts=attempts,
        )

    def _click_entry(
        self,
        page,
        before: PageState,
        *,
        warnings: list[str],
        step: int,
        attempts: list[str],
    ) -> ActionResult:
        for retry in range(3):
            if retry > 0:
                warnings.append(f"Step {step}: retrying Workday application entry click ({retry + 1}/3).")
                wait_for_application_entry_modal(page, warnings, timeout_ms=10_000)
            if _try_click_application_entry_modal(
                page,
                warnings=warnings,
                step=step,
                selector_attempts=attempts,
            ):
                if wait_for_apply_form_ready(page, warnings):
                    after = classify_page_state(page, observe_page_state(page, step))
                    return ActionResult(
                        status="clicked_and_form_ready",
                        reason="Clicked Workday Apply Manually and application form is ready.",
                        before_state=before,
                        after_state=after,
                        action="workday_apply_manually",
                        selector_attempts=attempts,
                    )
                after = classify_page_state(page, observe_page_state(page, step))
                if after.phase in ("application_form", "multi_step_form", "final_review_or_submit"):
                    return ActionResult(
                        status="clicked_and_form_ready",
                        reason="Clicked Workday entry and detected an application flow.",
                        before_state=before,
                        after_state=after,
                        action="workday_apply_manually",
                        selector_attempts=attempts,
                    )
                attempts.append(f"entry-retry:{retry + 1}: clicked but form not ready")

        return ActionResult(
            status="clicked_but_no_change",
            reason="Workday application entry button did not open the form.",
            before_state=before,
            after_state=classify_page_state(page, observe_page_state(page, step)),
            action="workday_apply_manually",
            selector_attempts=attempts,
        )


class GenericAdapter:
    """Generic deterministic actions for non-Workday application pages."""

    provider: ATSProvider = "generic"

    def act(
        self,
        page,
        state: PageState,
        *,
        warnings: list[str],
        step: int,
    ) -> ActionResult:
        attempts: list[str] = []
        before = state

        if state.phase == "application_entry_modal":
            if _try_click_application_entry_modal(
                page,
                warnings=warnings,
                step=step,
                selector_attempts=attempts,
            ):
                after = classify_page_state(page, observe_page_state(page, step))
                status: ActionStatus = (
                    "clicked_and_form_ready"
                    if after.phase in ("application_form", "multi_step_form", "final_review_or_submit")
                    or wait_for_apply_form_ready(page, warnings, timeout_ms=8_000)
                    else "clicked_but_no_change"
                )
                return ActionResult(
                    status=status,
                    reason="Clicked application entry modal.",
                    before_state=before,
                    after_state=classify_page_state(page, observe_page_state(page, step)),
                    action="generic_application_entry",
                    selector_attempts=attempts,
                )

        if state.phase in ("application_form", "multi_step_form") and _looks_like_auth_form(state):
            for name in _AUTH_SUBMIT_NAMES:
                if _click_flow_button(page, name):
                    warnings.append(f"Step {step}: auth flow clicked '{name}'.")
                    after = classify_page_state(page, observe_page_state(page, step))
                    status: ActionStatus = (
                        "clicked_but_no_change"
                        if after.screenshot_hash == before.screenshot_hash and after.url == before.url
                        else "clicked_and_form_ready"
                    )
                    if status == "clicked_but_no_change" and name.lower().startswith("create"):
                        if _should_try_sign_in_after_signup(before, after) and _try_click_auth_sign_in(page, attempts):
                            warnings.append(
                                f"Step {step}: Create Account did not advance; switched to Sign In."
                            )
                            after = classify_page_state(page, observe_page_state(page, step))
                            return ActionResult(
                                status="clicked_and_form_ready",
                                reason="Create Account did not advance; opened existing-account sign-in.",
                                before_state=before,
                                after_state=after,
                                action="auth_sign_in_pivot",
                                selector_attempts=attempts,
                            )
                    return ActionResult(
                        status=status,
                        reason=f"Clicked auth flow button '{name}'.",
                        before_state=before,
                        after_state=after,
                        action=f"auth:{name}",
                        selector_attempts=attempts,
                    )

        for name in _FLOW_BUTTON_NAMES:
            if _click_flow_button(page, name):
                warnings.append(f"Step {step}: heuristics clicked flow button '{name}'.")
                after = classify_page_state(page, observe_page_state(page, step))
                status: ActionStatus = (
                    "clicked_but_no_change"
                    if after.screenshot_hash == before.screenshot_hash and after.url == before.url
                    else "clicked_and_form_ready"
                )
                return ActionResult(
                    status=status,
                    reason=f"Clicked flow button '{name}'.",
                    before_state=before,
                    after_state=after,
                    action=f"flow:{name}",
                    selector_attempts=attempts,
                )

        if state.phase in ("loading", "job_description", "blocking_overlay", "stuck_or_unknown"):
            if _try_click_apply_on_job_page(
                page,
                warnings=warnings,
                step=step,
                selector_attempts=attempts,
            ):
                after = classify_page_state(page, observe_page_state(page, step))
                status: ActionStatus = (
                    "clicked_and_modal_opened"
                    if after.phase == "application_entry_modal"
                    else "clicked_and_form_ready"
                    if after.phase in ("application_form", "multi_step_form", "final_review_or_submit")
                    else "clicked_but_no_change"
                )
                return ActionResult(
                    status=status,
                    reason="Clicked generic Apply entry point.",
                    before_state=before,
                    after_state=after,
                    action="generic_apply",
                    selector_attempts=attempts,
                )

        return ActionResult(
            status="not_found",
            reason=f"Generic adapter found no deterministic action for {state.phase}.",
            before_state=before,
            after_state=classify_page_state(page, observe_page_state(page, step)),
            selector_attempts=attempts,
        )


def _adapter_for_state(state: PageState):
    if state.ats_provider == "workday":
        return WorkdayAdapter()
    return GenericAdapter()


def _same_action(a: VisionApplyAction | None, *, action: str, target_text: str) -> bool:
    return bool(
        a
        and a.action == action
        and (a.target_text or "").strip().lower() == target_text.strip().lower()
    )


def _is_apply_entry_action(action: VisionApplyAction) -> bool:
    target = (action.target_text or "").strip().lower()
    return action.action == "click" and target in {
        "apply",
        "apply now",
        "apply for this job",
        "start application",
    }


def _next_builtin_apply_skill(
    page,
    state: PageState,
    *,
    warnings: list[str],
    step: int,
    exclude: VisionApplyAction | None = None,
) -> VisionApplyAction | None:
    """Small universal browser skills that keep the agent from freezing.

    These are not a page state machine. They are safe Playwright primitives for
    obvious, repeatable controls that any apply agent should know how to use.
    """

    visible = {button.strip().lower(): button.strip() for button in state.visible_buttons if button.strip()}

    def visible_text(*names: str) -> str | None:
        for name in names:
            low = name.lower()
            if low in visible:
                return visible[low]
        for name in names:
            low = name.lower()
            for key, label in visible.items():
                if low in key:
                    return label
        return None

    if state.phase == "application_entry_modal":
        label = visible_text(*_APPLICATION_ENTRY_NAMES)
        if label:
            warnings.append(f"Step {step}: built-in skill selected modal entry '{label}'.")
            return VisionApplyAction(
                action="click",
                target_text=label,
                target_role="button",
                reason="Built-in apply skill: enter manual application flow.",
            )

    if state.phase in ("loading", "job_description", "blocking_overlay", "stuck_or_unknown"):
        label = visible_text(*_APPLY_ENTRY_NAMES)
        if label and not _same_action(exclude, action="click", target_text=label):
            warnings.append(f"Step {step}: built-in skill selected apply entry '{label}'.")
            return VisionApplyAction(
                action="click",
                target_text=label,
                target_role="button",
                reason="Built-in apply skill: click job apply button.",
            )

    if state.phase in ("application_form", "multi_step_form", "login_or_signup_required"):
        label = visible_text(*_AUTH_SUBMIT_NAMES, *_FLOW_BUTTON_NAMES)
        if label and not _same_action(exclude, action="click", target_text=label):
            warnings.append(f"Step {step}: built-in skill selected flow button '{label}'.")
            return VisionApplyAction(
                action="click",
                target_text=label,
                target_role="button",
                reason="Built-in apply skill: advance the application flow.",
            )

    return None


def _save_session_step(
    session_dir: Path | None,
    step_index: int,
    observation: PageObservation,
    action: VisionApplyAction | None,
    advanced: bool,
    *,
    state: PageState | None = None,
    result: ActionResult | None = None,
    stop_reason: str | None = None,
) -> None:
    if session_dir is None:
        return
    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        png_path = session_dir / f"step_{step_index:02d}.png"
        png_path.write_bytes(observation.screenshot_png)
        meta = {
            "step": step_index,
            "url": observation.url,
            "visible_buttons": observation.visible_buttons,
            "advanced": advanced,
            "state": {
                "phase": state.phase if state else None,
                "ats_provider": state.ats_provider if state else None,
                "hostname": state.hostname if state else None,
                "title": state.title if state else None,
                "form_fields": state.form_fields if state else [],
                "modal_texts": state.modal_texts if state else [],
                "screenshot_hash": state.screenshot_hash if state else None,
            },
            "action_result": {
                "status": result.status if result else None,
                "reason": result.reason if result else None,
                "action": result.action if result else None,
                "selector_attempts": result.selector_attempts if result else [],
                "before_phase": result.before_state.phase if result and result.before_state else None,
                "after_phase": result.after_state.phase if result and result.after_state else None,
                "before_url": result.before_state.url if result and result.before_state else None,
                "after_url": result.after_state.url if result and result.after_state else None,
                "before_hash": result.before_state.screenshot_hash if result and result.before_state else None,
                "after_hash": result.after_state.screenshot_hash if result and result.after_state else None,
            },
            "stop_reason": stop_reason,
            "action": {
                "action": action.action if action else None,
                "target_text": action.target_text if action else None,
                "key": action.key if action else None,
                "x": action.x if action else None,
                "y": action.y if action else None,
                "reason": action.reason if action else None,
            },
        }
        (session_dir / f"step_{step_index:02d}.json").write_text(
            json.dumps(meta, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        logger.debug("Failed to save harness session step: %s", e)


def _force_stuck_recovery(page, warnings: list[str]) -> bool:
    warnings.append("Harness stuck on identical page state — forcing Escape and scroll recovery.")
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(_MIN_DOM_MS)
        page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.5))")
        _wait_dom_settle(page)
        return True
    except Exception:
        return False


class _LegacyBrowserHarness:
    """Legacy observe-plan-act loop kept temporarily for reference."""

    def run_until_ready(
        self,
        page,
        context: HarnessContext,
        *,
        fill_fields_fn: Callable,
        fields_filled: list[str],
        warnings: list[str],
    ) -> HarnessResult:
        settings = get_settings()
        max_steps = settings.apply_harness_max_steps
        stuck_threshold = settings.apply_harness_stuck_threshold
        session_dir = context.session_dir

        if context.vision_enabled:
            warnings.append(
                f"Vision assist enabled ({settings.resolved_apply_vision_model()})."
            )

        wait_for_apply_page_ready(page, warnings)

        screenshot_hashes: deque[str] = deque(maxlen=stuck_threshold)
        steps_taken = 0

        for step in range(max_steps):
            observation = observe_page_state(page, step)
            if not observation.visible_buttons:
                warnings.append(f"Step {step}: page UI not visible yet — waiting.")
                _wait_dom_settle(page)
                wait_for_apply_page_ready(page, warnings, timeout_ms=20_000)
                observation = observe_page_state(page, step)
            screenshot_hash = hashlib.md5(observation.screenshot_png).hexdigest()
            screenshot_hashes.append(screenshot_hash)

            if (
                len(screenshot_hashes) == stuck_threshold
                and len(set(screenshot_hashes)) == 1
            ):
                advanced = _force_stuck_recovery(page, warnings)
                _save_session_step(session_dir, step, observation, None, advanced)
                if advanced:
                    steps_taken += 1
                    screenshot_hashes.clear()
                    continue
                break

            dismiss_blocking_ui(page, warnings)
            fill_fields_fn(page, fields_filled, warnings)

            submit_selector = find_submit_selector(page)
            if submit_selector:
                return HarnessResult(
                    submit_selector=submit_selector,
                    session_dir=str(session_dir) if session_dir else None,
                    steps_taken=steps_taken,
                )

            advanced = False
            vision_action: VisionApplyAction | None = None
            pre_action_png = observation.screenshot_png

            if _application_entry_modal_open(page):
                wait_for_application_entry_modal(page, warnings)
                advanced = _try_click_application_entry_modal(
                    page, warnings=warnings, step=step
                )

            if not advanced:
                for name in _FLOW_BUTTON_NAMES:
                    if _click_flow_button(page, name):
                        warnings.append(f"Step {step}: heuristics clicked flow button '{name}'.")
                        advanced = True
                        break
            if not advanced:
                advanced = _try_click_apply_on_job_page(page, warnings=warnings, step=step)

            if advanced and not _page_changed(page, pre_action_png):
                warnings.append(f"Step {step}: click did not change the page — treating as no advance.")
                advanced = False

            if not advanced and context.vision_enabled:
                try:
                    vision_action = run_coro_sync(
                        decide_apply_action(
                            screenshot_png=observation.screenshot_png,
                            page_url=observation.url,
                            user_name=context.user_name,
                            user_email=context.user_email,
                            user_links=context.user_links,
                            a11y_snapshot=observation.a11y_snapshot,
                            visible_buttons=observation.visible_buttons,
                        )
                    )
                    warnings.append(
                        f"Step {step}: vision returned {vision_action.action}"
                        + (f" — {vision_action.reason}" if vision_action.reason else ".")
                    )
                    advanced = execute_browser_action(
                        page,
                        vision_action,
                        fields_filled=fields_filled,
                        warnings=warnings,
                    )
                    if advanced and not _page_changed(page, pre_action_png):
                        warnings.append(f"Step {step}: vision click did not change the page.")
                        advanced = False
                    if advanced:
                        warnings.append(f"Step {step}: vision action advanced the page.")
                    elif vision_action.action == "done":
                        warnings.append(f"Step {step}: vision returned done (continuing).")
                except Exception as e:
                    logger.warning("Vision apply step failed: %s", e)
                    warnings.append(f"Step {step}: vision assist failed: {e}")

            _save_session_step(session_dir, step, observation, vision_action, advanced)

            if not advanced:
                if vision_action and vision_action.action == "done":
                    submit_selector = find_submit_selector(page)
                    if submit_selector:
                        return HarnessResult(
                            submit_selector=submit_selector,
                            session_dir=str(session_dir) if session_dir else None,
                            steps_taken=steps_taken,
                        )
                    continue
                if step < 4 and not observation.visible_buttons:
                    warnings.append(f"Step {step}: no advance — page still empty, retrying.")
                    _wait_dom_settle(page)
                    continue
                warnings.append(f"Step {step}: no advance — stopping harness.")
                break

            steps_taken += 1

        return HarnessResult(
            submit_selector=find_submit_selector(page),
            session_dir=str(session_dir) if session_dir else None,
            steps_taken=steps_taken,
        )


class BrowserHarness:
    """Goal-driven apply operator for external ATS/application sites.

    The model is the planner. This harness supplies Playwright-style browser
    skills, page evidence, trace history, and hard safety rails.
    """

    def run_until_ready(
        self,
        page,
        context: HarnessContext,
        *,
        fill_fields_fn: Callable,
        fields_filled: list[str],
        warnings: list[str],
    ) -> HarnessResult:
        settings = get_settings()
        max_steps = settings.apply_harness_max_steps
        session_dir = context.session_dir

        if not context.vision_enabled:
            warnings.append(
                "Apply browser agent is disabled; using built-in browser skills only."
            )
        else:
            warnings.append(
                f"Apply browser agent enabled ({settings.resolved_apply_vision_model()})."
            )
        wait_for_apply_page_ready(page, warnings)

        steps_taken = 0
        action_history: deque[str] = deque(maxlen=12)

        for step in range(max_steps):
            observation = observe_page_state(page, step)
            if not observation.visible_buttons:
                warnings.append(f"Step {step}: page UI not visible yet - waiting.")
                _wait_dom_settle(page)
                wait_for_apply_page_ready(page, warnings, timeout_ms=20_000)
                observation = observe_page_state(page, step)

            dismiss_blocking_ui(page, warnings)
            observation = observe_page_state(page, step)
            state = classify_page_state(page, observation)
            submit_selector = find_submit_selector(page)
            if submit_selector or state.phase == "final_review_or_submit":
                result = ActionResult(
                    status="done",
                    reason="Final submit/review control detected; stopping before submit.",
                    before_state=state,
                    after_state=state,
                    action="stop_before_submit",
                )
                _save_session_step(
                    session_dir,
                    step,
                    observation,
                    None,
                    False,
                    state=state,
                    result=result,
                    stop_reason=result.reason,
                )
                return HarnessResult(
                    submit_selector=submit_selector,
                    session_dir=str(session_dir) if session_dir else None,
                    steps_taken=steps_taken,
                )

            if state.phase in ("application_form", "multi_step_form", "login_or_signup_required"):
                fill_fields_fn(page, fields_filled, warnings)
                observation = observe_page_state(page, step)
                state = classify_page_state(page, observation)
                submit_selector = find_submit_selector(page)
                if submit_selector or state.phase == "final_review_or_submit":
                    result = ActionResult(
                        status="done",
                        reason="Form fields filled and final submit/review control detected.",
                        before_state=state,
                        after_state=state,
                        action="stop_before_submit",
                    )
                    _save_session_step(
                        session_dir,
                        step,
                        observation,
                        None,
                        False,
                        state=state,
                        result=result,
                        stop_reason=result.reason,
                    )
                    return HarnessResult(
                        submit_selector=submit_selector,
                        session_dir=str(session_dir) if session_dir else None,
                        steps_taken=steps_taken,
                    )

            pre_action_png = observation.screenshot_png
            before_state = state
            agent_action: VisionApplyAction | None = None
            if context.vision_enabled:
                try:
                    agent_action = run_coro_sync(
                        decide_apply_action(
                            screenshot_png=observation.screenshot_png,
                            page_url=observation.url,
                            user_name=context.user_name,
                            user_email=context.user_email,
                            user_links=context.user_links,
                            a11y_snapshot=observation.a11y_snapshot,
                            visible_buttons=observation.visible_buttons,
                            page_state=f"{state.phase}:{state.ats_provider}",
                            form_fields=state.form_fields,
                            modal_texts=state.modal_texts,
                            filled_fields=fields_filled,
                            action_history=list(action_history),
                        )
                    )
                except Exception as e:
                    logger.warning("Apply browser agent step failed: %s", e)
                    warnings.append(f"Step {step}: apply browser agent failed: {e}")

            if agent_action is None or agent_action.action == "none":
                fallback = _next_builtin_apply_skill(page, state, warnings=warnings, step=step)
                if fallback is not None:
                    agent_action = fallback

            if agent_action is None:
                if state.phase == "loading":
                    warnings.append(f"Step {step}: page still loading; waiting instead of stopping.")
                    _wait_dom_settle(page)
                    continue
                result = ActionResult(
                    status="not_found",
                    reason="No browser skill or agent action was available.",
                    before_state=before_state,
                    after_state=state,
                    action="agent:none",
                )
                _save_session_step(
                    session_dir,
                    step,
                    observation,
                    None,
                    False,
                    state=state,
                    result=result,
                    stop_reason=result.reason,
                )
                warnings.append(f"Step {step}: stopping - {result.reason}")
                break

            if state.phase == "application_entry_modal" and _action_would_close_application_modal(agent_action):
                fallback = _next_builtin_apply_skill(
                    page,
                    state,
                    warnings=warnings,
                    step=step,
                    exclude=None,
                )
                if fallback is not None:
                    warnings.append(
                        f"Step {step}: prevented modal-closing action; using '{fallback.target_text}'."
                    )
                    agent_action = fallback

            warnings.append(
                f"Step {step}: apply agent returned {agent_action.action}"
                + (f" - {agent_action.reason}" if agent_action.reason else ".")
            )

            if agent_action.action == "done":
                result = ActionResult(
                    status="done",
                    reason=agent_action.reason or "Apply agent says the form is ready for review.",
                    before_state=before_state,
                    after_state=state,
                    action="agent:done",
                    vision_action=agent_action,
                )
                _save_session_step(
                    session_dir,
                    step,
                    observation,
                    agent_action,
                    False,
                    state=state,
                    result=result,
                    stop_reason=result.reason,
                )
                return HarnessResult(
                    submit_selector=find_submit_selector(page),
                    session_dir=str(session_dir) if session_dir else None,
                    steps_taken=steps_taken,
                )

            if agent_action.action in ("handoff", "none"):
                result = ActionResult(
                    status="blocked" if agent_action.action == "handoff" else "not_found",
                    reason=agent_action.reason or "Apply agent found no safe browser action.",
                    before_state=before_state,
                    after_state=state,
                    action=f"agent:{agent_action.action}",
                    vision_action=agent_action,
                )
                _save_session_step(
                    session_dir,
                    step,
                    observation,
                    agent_action,
                    False,
                    state=state,
                    result=result,
                    stop_reason=result.reason,
                )
                warnings.append(f"Step {step}: stopping - {result.reason}")
                break

            executed = execute_browser_action(
                page,
                agent_action,
                fields_filled=fields_filled,
                warnings=warnings,
            )
            if executed and _is_apply_entry_action(agent_action):
                if wait_for_application_entry_modal(page, warnings, timeout_ms=20_000):
                    warnings.append(f"Step {step}: apply entry opened application modal.")
                elif wait_for_apply_form_ready(page, warnings, timeout_ms=12_000):
                    warnings.append(f"Step {step}: apply entry opened application form.")
            if not executed:
                fallback = _next_builtin_apply_skill(
                    page,
                    state,
                    warnings=warnings,
                    step=step,
                    exclude=agent_action,
                )
                if fallback is not None:
                    warnings.append(
                        f"Step {step}: agent action failed; trying built-in skill {fallback.action}"
                        + (f" '{fallback.target_text}'." if fallback.target_text else ".")
                    )
                    agent_action = fallback
                    executed = execute_browser_action(
                        page,
                        agent_action,
                        fields_filled=fields_filled,
                        warnings=warnings,
                    )
            changed = _page_changed(page, pre_action_png) if executed else False
            after_state = classify_page_state(page, observe_page_state(page, step))
            result = ActionResult(
                status="clicked_and_form_ready"
                if changed
                else "clicked_but_no_change"
                if executed
                else "not_found",
                reason=(
                    "Agent action changed the page."
                    if changed
                    else "Agent action executed but the page did not visibly change."
                    if executed
                    else "Agent action could not be executed by available browser skills."
                ),
                before_state=before_state,
                after_state=after_state,
                action=f"agent:{agent_action.action}",
                vision_action=agent_action,
            )
            action_history.append(
                f"step {step}: {agent_action.action}"
                f" target={agent_action.target_text or agent_action.option_text or agent_action.key or ''}"
                f" executed={executed} changed={changed} url={after_state.url}"
            )

            _save_session_step(
                session_dir,
                step,
                observation,
                agent_action,
                executed,
                state=state,
                result=result,
            )

            if not executed:
                warnings.append(
                    f"Step {step}: browser skill could not execute {agent_action.action}; asking agent again."
                )
                _wait_dom_settle(page, ms=1_000)
                continue

            steps_taken += 1
            submit_selector = find_submit_selector(page)
            if submit_selector or after_state.phase == "final_review_or_submit":
                return HarnessResult(
                    submit_selector=submit_selector,
                    session_dir=str(session_dir) if session_dir else None,
                    steps_taken=steps_taken,
                )

        return HarnessResult(
            submit_selector=find_submit_selector(page),
            session_dir=str(session_dir) if session_dir else None,
            steps_taken=steps_taken,
        )
