"""Apply agent: open job posting, start apply flow, fill forms, stop before submit."""

from __future__ import annotations

import json
import logging
import secrets
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from ..config import get_settings
from ..models import Document, Job, User
from ..services.application_defaults import (
    answer_candidates,
    match_option,
    merge_application_defaults,
    resolve_canonical_key,
    resolve_value,
    saved_answers,
)
from .browser_harness import (
    BrowserHarness,
    HarnessContext,
    wait_for_apply_page_ready,
    _wait_dom_settle,
)
from .browser_use_apply_runner import (
    BrowserUseApplyInput,
    confirm_browser_use_submit,
    run_browser_use_apply,
)
from .jd_fetch_agent import looks_like_login_wall
from .playwright_runner import run_playwright_in_thread

logger = logging.getLogger(__name__)

APPLY_META_PREFIX = "apply_meta:"

_NAME_HINTS = ("name", "full name", "first name", "your name", "legal name")
_EMAIL_HINTS = ("email", "e-mail")
_PASSWORD_HINTS = ("password", "new password", "verify password", "confirm password")
_LINKEDIN_HINTS = ("linkedin",)
_GITHUB_HINTS = ("github",)
_PORTFOLIO_HINTS = ("portfolio", "website", "url")
_COVER_HINTS = ("cover letter", "cover", "motivation", "why are you", "additional information")
_CONSENT_HINTS = ("consent", "terms and conditions", "privacy", "i have read")

_LOGIN_NAV_TIMEOUT_MS = 10 * 60 * 1000

_LINKEDIN_EASY_APPLY_SELECTORS = (
    "button.jobs-apply-button",
    'button[aria-label*="Easy Apply"]',
    'button:has-text("Easy Apply")',
)

@dataclass(slots=True)
class ApplyPrepareResult:
    page_url: str
    fields_filled: list[str] = field(default_factory=list)
    screenshot_path: str | None = None
    submit_selector: str | None = None
    apply_flow: str | None = None
    session_dir: str | None = None
    warnings: list[str] = field(default_factory=list)
    browser_available: bool = True
    generated_account_password: str | None = None
    browser_engine: str = "playwright"
    browser_use_session_id: str | None = None
    browser_use_live_url: str | None = None
    browser_use_output: dict[str, Any] | None = None
    browser_use_mode: str | None = None


@dataclass(slots=True)
class _PrepareFormInput:
    user_name: str
    user_email: str | None
    user_links: dict[str, str]
    years_experience: float | None
    application_defaults: dict[str, str]
    saved_application_answers: dict[str, str]
    prior_apply_meta: dict[str, Any] | None
    job_id: int
    application_id: int
    job_url: str
    resume_file_path: str | None
    resume_content: str | None
    cover_content: str | None
    account_password: str
    browser_dir: Path
    screenshots_dir: Path
    headless: bool
    vision_enabled: bool


def encode_apply_meta(result: ApplyPrepareResult) -> str:
    payload: dict[str, Any] = {
        "prepared_at": datetime.now(UTC).isoformat(),
        "page_url": result.page_url,
        "fields_filled": result.fields_filled,
        "screenshot_path": result.screenshot_path,
        "submit_selector": result.submit_selector,
        "apply_flow": result.apply_flow,
        "session_dir": result.session_dir,
        "warnings": result.warnings,
        "generated_account_password": result.generated_account_password,
        "browser_engine": result.browser_engine,
        "browser_use_session_id": result.browser_use_session_id,
        "browser_use_live_url": result.browser_use_live_url,
        "browser_use_output": result.browser_use_output,
        "browser_use_mode": result.browser_use_mode,
    }
    return APPLY_META_PREFIX + json.dumps(payload, separators=(",", ":"))


def decode_apply_meta(notes: str | None) -> dict[str, Any] | None:
    if not notes or not notes.startswith(APPLY_META_PREFIX):
        return None
    try:
        obj = json.loads(notes[len(APPLY_META_PREFIX) :])
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _label_matches(label: str, hints: tuple[str, ...]) -> bool:
    low = label.lower()
    return any(h in low for h in hints)


def _write_temp_text_file(text: str, suffix: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=suffix, delete=False)
    tmp.write(text)
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def _generate_account_password() -> str:
    """Generate a Workday-compatible temporary password for ATS account creation."""

    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    core = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"Jb{core}!7a"


def _browser_use_input(payload: _PrepareFormInput) -> BrowserUseApplyInput:
    return BrowserUseApplyInput(
        user_name=payload.user_name,
        user_email=payload.user_email,
        user_links=payload.user_links,
        years_experience=payload.years_experience,
        application_defaults=payload.application_defaults,
        saved_application_answers=payload.saved_application_answers,
        job_id=payload.job_id,
        application_id=payload.application_id,
        job_url=payload.job_url,
        resume_file_path=payload.resume_file_path,
        resume_content=payload.resume_content,
        cover_content=payload.cover_content,
        account_password=payload.account_password,
        browser_dir=payload.browser_dir,
        screenshots_dir=payload.screenshots_dir,
        headless=payload.headless,
    )


def _page_body_preview(page) -> str:
    try:
        body = page.query_selector("body")
        return body.inner_text()[:2_000] if body else ""
    except Exception:
        return ""


def _on_login_wall(page) -> bool:
    return looks_like_login_wall(page.url, _page_body_preview(page))


def _wait_for_manual_login(page, job_url: str, warnings: list[str]) -> None:
    """Let the user finish sign-in before automation continues."""

    if not _on_login_wall(page):
        return

    try:
        page.bring_to_front()
    except Exception:
        pass
    warnings.append("Sign in if prompted. JobBee will continue once the application page loads.")

    try:
        page.wait_for_function(
            """() => {
                const url = location.href.toLowerCase();
                const loginHints = ['login', 'authwall', 'checkpoint', 'signin', 'sign-in', 'uas/'];
                if (loginHints.some((hint) => url.includes(hint))) return false;
                const text = (document.body && document.body.innerText || '').toLowerCase().slice(0, 2000);
                const copyHints = [
                    'sign in to linkedin',
                    'join linkedin',
                    'log in to continue',
                    'sign in to view',
                ];
                return copyHints.filter((hint) => text.includes(hint)).length < 2;
            }""",
            timeout=_LOGIN_NAV_TIMEOUT_MS,
        )
    except Exception:
        warnings.append("Timed out waiting for sign-in; continuing with the current page.")

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass

    if "jobs/view" in job_url and "jobs/view" in page.url:
        return

    if "jobs/view" in job_url and "jobs/view" not in page.url:
        try:
            page.goto(job_url, wait_until="domcontentloaded", timeout=45_000)
        except Exception:
            warnings.append("Could not reload the job posting after sign-in.")


def _field_label(el) -> str:
    try:
        return el.evaluate(
            """(el) => {
                const id = el.id;
                if (id) {
                    const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                    if (label && label.innerText) return label.innerText;
                }
                const parentLabel = el.closest('label');
                if (parentLabel && parentLabel.innerText) return parentLabel.innerText;
                return el.getAttribute('aria-label')
                    || el.getAttribute('placeholder')
                    || el.getAttribute('name')
                    || el.getAttribute('id')
                    || '';
            }"""
        )
    except Exception:
        return (
            el.get_attribute("aria-label")
            or el.get_attribute("placeholder")
            or el.get_attribute("name")
            or el.get_attribute("id")
            or ""
        )


def _control_label(page, el) -> str:
    label = _field_label(el)
    if label.strip():
        return label
    try:
        return el.evaluate(
            """(el) => {
                const fieldset = el.closest('fieldset');
                if (fieldset) {
                    const legend = fieldset.querySelector('legend');
                    if (legend && legend.innerText) return legend.innerText;
                }
                const group = el.closest('[role="group"], [role="radiogroup"]');
                if (group) {
                    const labelled = group.getAttribute('aria-labelledby');
                    if (labelled) {
                        const node = document.getElementById(labelled);
                        if (node && node.innerText) return node.innerText;
                    }
                }
                return '';
            }"""
        )
    except Exception:
        return ""


def _select_option_labels(el) -> list[str]:
    try:
        return el.evaluate(
            """(el) => [...el.options]
                .map((o) => (o.label || o.textContent || o.value || '').trim())
                .filter(Boolean)"""
        )
    except Exception:
        return []


def _resolve_application_answer(payload: _PrepareFormInput, label: str, key: str | None) -> tuple[str | None, str | None]:
    if not key:
        return None, None
    canonical = resolve_value(
        key,
        defaults=payload.application_defaults,
        saved=payload.saved_application_answers,
        label=label,
        years_experience=payload.years_experience,
        job_url=payload.job_url,
    )
    if not canonical:
        return key, None
    return key, canonical


def _try_fill_select(el, canonical_key: str, canonical_value: str) -> bool:
    options = _select_option_labels(el)
    if not options:
        return False
    picked = match_option(options, answer_candidates(canonical_key, canonical_value))
    if not picked:
        picked = match_option(options, [canonical_value])
    if not picked:
        return False
    try:
        el.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    for kwargs in ({"label": picked}, {"value": picked}):
        try:
            el.select_option(**kwargs, timeout=5_000)
            return True
        except Exception:
            continue
    return False


def _try_fill_radio_group(page, group_name: str, canonical_key: str, canonical_value: str) -> bool:
    candidates = answer_candidates(canonical_key, canonical_value)
    if canonical_value not in candidates:
        candidates = [canonical_value, *candidates]
    for candidate in candidates:
        try:
            radio = page.get_by_role("radio", name=candidate)
            if radio.count() == 0:
                continue
            target = radio.first
            if not target.is_visible():
                continue
            target.scroll_into_view_if_needed(timeout=3_000)
            target.check(timeout=5_000)
            page.wait_for_timeout(300)
            return True
        except Exception:
            continue

    try:
        radios = page.locator(f'input[type="radio"][name="{group_name}"]')
        count = radios.count()
    except Exception:
        return False

    for index in range(count):
        try:
            radio = radios.nth(index)
            option_label = radio.evaluate(
                """(el) => {
                    const id = el.id;
                    if (id) {
                        const label = document.querySelector(`label[for="${CSS.escape(id)}"]`);
                        if (label) return label.innerText.trim();
                    }
                    const parent = el.closest('label');
                    return parent ? parent.innerText.trim() : '';
                }"""
            )
            picked = match_option([option_label], candidates) if option_label else None
            if not picked:
                continue
            radio.scroll_into_view_if_needed(timeout=3_000)
            radio.check(timeout=5_000)
            page.wait_for_timeout(300)
            return True
        except Exception:
            continue
    return False


def _fill_select_fields(page, payload: _PrepareFormInput, fields_filled: list[str]) -> None:
    for el in page.query_selector_all("select"):
        try:
            label = _control_label(page, el)
            key = resolve_canonical_key(label)
            key, canonical = _resolve_application_answer(payload, label, key)
            if not key or not canonical:
                continue
            if key in fields_filled:
                continue
            if _try_fill_select(el, key, canonical):
                fields_filled.append(key)
        except Exception:
            continue


def _fill_radio_fields(page, payload: _PrepareFormInput, fields_filled: list[str]) -> None:
    try:
        group_names: list[str] = page.evaluate(
            """() => {
                const names = new Set();
                for (const el of document.querySelectorAll('input[type="radio"][name]')) {
                    if (el.offsetParent || el.getAttribute('type') === 'radio') names.add(el.name);
                }
                return [...names];
            }"""
        )
    except Exception:
        return

    for group_name in group_names:
        try:
            first = page.query_selector(f'input[type="radio"][name="{group_name}"]')
            if first is None:
                continue
            label = _control_label(page, first)
            key = resolve_canonical_key(label)
            key, canonical = _resolve_application_answer(payload, label, key)
            if not key or not canonical:
                continue
            fill_key = f"{key}:{group_name}"
            if fill_key in fields_filled or key in fields_filled:
                continue
            if _try_fill_radio_group(page, group_name, key, canonical):
                fields_filled.append(fill_key)
        except Exception:
            continue


def _try_check_checkbox(el) -> bool:
    try:
        el.scroll_into_view_if_needed(timeout=3_000)
    except Exception:
        pass
    try:
        el.check(timeout=5_000)
        return True
    except Exception:
        pass
    try:
        el.click(timeout=5_000)
        return True
    except Exception:
        return False


def _fill_checkbox_fields(page, fields_filled: list[str], warnings: list[str]) -> None:
    for el in page.query_selector_all('input[type="checkbox"]'):
        try:
            label = _control_label(page, el) or _field_label(el)
            low = label.lower()
            if "robot" in low or "do not enter" in low:
                continue
            if not any(hint in low for hint in _CONSENT_HINTS):
                continue
            key = "account_creation_consent"
            if key in fields_filled:
                continue
            try:
                if el.is_checked():
                    fields_filled.append(key)
                    continue
            except Exception:
                pass
            if _try_check_checkbox(el):
                fields_filled.append(key)
                warnings.append("Accepted the ATS account creation consent checkbox.")
        except Exception:
            continue


def _fill_application_fields(
    page,
    payload: _PrepareFormInput,
    fields_filled: list[str],
    warnings: list[str],
) -> None:
    """Fill visible application fields from the user profile and tailored docs."""

    for el in page.query_selector_all("input, textarea"):
        try:
            tag = el.evaluate("el => el.tagName").lower()
            input_type = (el.get_attribute("type") or "").lower()
            if tag == "input" and input_type in ("hidden", "submit", "button", "file", "checkbox", "radio"):
                continue

            label = _field_label(el)
            value: str | None = None
            field_name: str | None = None

            canonical_key = resolve_canonical_key(label)
            if canonical_key == "phone":
                _, value = _resolve_application_answer(payload, label, "phone")
                field_name = "phone"
            elif _label_matches(label, _NAME_HINTS) and payload.user_name:
                value = payload.user_name
                field_name = "name"
            elif _label_matches(label, _EMAIL_HINTS) and payload.user_email:
                value = payload.user_email
                field_name = "email"
            elif input_type == "password" or _label_matches(label, _PASSWORD_HINTS):
                value = payload.account_password
                label_low = label.lower()
                if any(hint in label_low for hint in ("verify", "confirm", "re-enter", "repeat")):
                    field_name = "account_password_verify"
                else:
                    field_name = "account_password"
            elif _label_matches(label, _LINKEDIN_HINTS) and payload.user_links.get("linkedin"):
                value = payload.user_links["linkedin"]
                field_name = "linkedin"
            elif _label_matches(label, _GITHUB_HINTS) and payload.user_links.get("github"):
                value = payload.user_links["github"]
                field_name = "github"
            elif _label_matches(label, _PORTFOLIO_HINTS) and payload.user_links.get("portfolio"):
                value = payload.user_links["portfolio"]
                field_name = "portfolio"
            elif _label_matches(label, _COVER_HINTS) and payload.cover_content:
                value = payload.cover_content
                field_name = "cover_letter"
            elif canonical_key == "years_experience":
                _, value = _resolve_application_answer(payload, label, "years_experience")
                field_name = "years_experience"

            if value and field_name and field_name not in fields_filled:
                if tag == "textarea" or input_type not in ("submit", "button"):
                    try:
                        el.scroll_into_view_if_needed(timeout=3_000)
                    except Exception:
                        pass
                    el.fill(value)
                    fields_filled.append(field_name)
                    if field_name.startswith("account_password") and "ats_account_password" not in fields_filled:
                        fields_filled.append("ats_account_password")
                        warnings.append(
                            "Created a temporary ATS account password for this application. "
                            f"Save it if the employer asks you to sign back in: {payload.account_password}"
                        )
        except Exception:
            continue

    _fill_select_fields(page, payload, fields_filled)
    _fill_radio_fields(page, payload, fields_filled)
    _fill_checkbox_fields(page, fields_filled, warnings)

    resume_path: Path | None = None
    if payload.resume_file_path and Path(payload.resume_file_path).is_file():
        resume_path = Path(payload.resume_file_path)
    elif payload.resume_content:
        resume_path = _write_temp_text_file(payload.resume_content, "_resume.txt")

    if resume_path is not None:
        for file_input in page.query_selector_all('input[type="file"]')[:3]:
            try:
                file_input.set_input_files(str(resume_path))
                if "resume_upload" not in fields_filled:
                    fields_filled.append("resume_upload")
                break
            except Exception:
                continue
        else:
            if page.query_selector_all('input[type="file"]'):
                warnings.append("Found a file input but could not attach the tailored resume.")


def _is_linkedin_job_page(url: str) -> bool:
    low = url.lower()
    return "linkedin.com" in low and "jobs/view" in low


def _try_open_easy_apply(page, warnings: list[str]) -> bool:
    if not _is_linkedin_job_page(page.url):
        return False

    try:
        page.wait_for_load_state("domcontentloaded", timeout=15_000)
    except Exception:
        pass

    try:
        easy_apply = page.get_by_role("button", name="Easy Apply")
        if easy_apply.count() > 0:
            easy_apply.first.click(timeout=8_000)
            warnings.append("Started LinkedIn Easy Apply.")
            page.locator('div[role="dialog"]').first.wait_for(state="visible", timeout=12_000)
            return True
    except Exception:
        pass

    for selector in _LINKEDIN_EASY_APPLY_SELECTORS:
        try:
            btn = page.locator(selector).first
            btn.wait_for(state="visible", timeout=5_000)
            text = btn.inner_text(timeout=2_000).strip().lower()
            if "easy apply" not in text:
                continue
            btn.click(timeout=8_000)
            warnings.append("Started LinkedIn Easy Apply.")
            page.locator('div[role="dialog"]').first.wait_for(state="visible", timeout=12_000)
            return True
        except Exception:
            continue

    return False


def _follow_apply_navigation(page, context, click_action: Callable[[], None], warnings: list[str]):
    """Click Apply and follow same-tab navigation or a new browser tab."""

    original_url = page.url
    try:
        with context.expect_page(timeout=15_000) as page_info:
            click_action()
        target = page_info.value
        target.wait_for_load_state("domcontentloaded", timeout=45_000)
        wait_for_apply_page_ready(target, warnings)
        warnings.append(f"Opened external application form: {target.url}")
        return target
    except Exception:
        logger.debug("Apply click did not open a new tab", exc_info=True)

    try:
        click_action()
        _wait_dom_settle(page)
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:
            pass
        if page.url != original_url:
            wait_for_apply_page_ready(page, warnings)
            warnings.append(f"Navigated to external application form: {page.url}")
            return page
    except Exception:
        logger.debug("Apply click navigation failed", exc_info=True)

    return None


def _external_apply_candidates(page) -> list:
    candidates: list = []
    try:
        buttons = page.locator("button.jobs-apply-button, a.jobs-apply-button")
        for i in range(buttons.count()):
            btn = buttons.nth(i)
            try:
                text = btn.inner_text(timeout=1_000).strip().lower()
            except Exception:
                continue
            if "easy apply" in text:
                continue
            if "apply" in text:
                candidates.append(btn)
    except Exception:
        pass

    for role in ("button", "link"):
        for name in ("Apply", "Apply now", "Apply on company website"):
            try:
                loc = page.get_by_role(role, name=name)
                if loc.count() > 0:
                    candidates.append(loc.first)
            except Exception:
                continue

    return candidates


def _try_open_external_apply(page, context, warnings: list[str]):
    if not _is_linkedin_job_page(page.url):
        return None

    seen: set[str] = set()
    for btn in _external_apply_candidates(page):
        try:
            btn.wait_for(state="visible", timeout=5_000)
            marker = btn.evaluate("el => el.outerHTML.slice(0, 160)")
            if marker in seen:
                continue
            seen.add(marker)

            def _click(button=btn) -> None:
                button.click(timeout=8_000)

            target = _follow_apply_navigation(page, context, _click, warnings)
            if target is not None:
                return target
        except Exception:
            continue

    return None


def _start_apply_flow(page, context, payload: _PrepareFormInput, warnings: list[str], *, headless: bool):
    """Detect Easy Apply vs external Apply and open the application form."""

    if _is_linkedin_job_page(page.url):
        if _try_open_easy_apply(page, warnings):
            return page, "easy_apply"

        external_page = _try_open_external_apply(page, context, warnings)
        if external_page is not None:
            if not headless:
                _wait_for_manual_login(external_page, payload.job_url, warnings)
            return external_page, "external_apply"

        warnings.append(
            "Could not find Easy Apply or external Apply on this LinkedIn posting. "
            "Open the apply flow manually in the browser."
        )
        return page, "linkedin_unknown"

    return page, "direct"


def _advance_application_form(
    page,
    payload: _PrepareFormInput,
    fields_filled: list[str],
    warnings: list[str],
) -> str | None:
    """Fill fields and step through multi-page application forms via the browser harness."""

    settings = get_settings()
    session_dir: Path | None = None
    if settings.apply_harness_save_session:
        app_id = payload.application_id or payload.job_id
        session_dir = payload.screenshots_dir / "sessions" / str(app_id)

    def _fill(page_obj, filled: list[str], warns: list[str]) -> None:
        _fill_application_fields(page_obj, payload, filled, warns)

    harness = BrowserHarness()
    result = harness.run_until_ready(
        page,
        HarnessContext(
            user_name=payload.user_name,
            user_email=payload.user_email,
            user_links=payload.user_links,
            application_id=payload.application_id or payload.job_id,
            vision_enabled=payload.vision_enabled,
            session_dir=session_dir,
        ),
        fill_fields_fn=_fill,
        fields_filled=fields_filled,
        warnings=warnings,
    )
    if result.session_dir:
        warnings.append(f"Harness session trace saved to {result.session_dir}.")
    return result.submit_selector


def _prepare_application_form_sync(payload: _PrepareFormInput) -> ApplyPrepareResult:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return ApplyPrepareResult(
            page_url=payload.job_url,
            browser_available=False,
            warnings=[
                "Playwright is not installed. Run: pip install playwright && playwright install chromium",
            ],
        )

    payload.browser_dir.mkdir(parents=True, exist_ok=True)
    payload.screenshots_dir.mkdir(parents=True, exist_ok=True)

    fields_filled: list[str] = []
    warnings: list[str] = []
    page_url = payload.job_url
    screenshot_path: str | None = None
    submit_selector: str | None = None
    apply_flow: str | None = None
    session_dir: str | None = None

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(payload.browser_dir),
            headless=payload.headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        form_page = page
        try:
            page.goto(payload.job_url, wait_until="domcontentloaded", timeout=45_000)
            page_url = page.url

            if not payload.headless:
                _wait_for_manual_login(page, payload.job_url, warnings)

            form_page, apply_flow = _start_apply_flow(
                page, context, payload, warnings, headless=payload.headless
            )
            page_url = form_page.url

            submit_selector = _advance_application_form(
                form_page,
                payload,
                fields_filled,
                warnings,
            )
            settings = get_settings()
            if settings.apply_harness_save_session:
                app_id = payload.application_id or payload.job_id
                session_dir = str(payload.screenshots_dir / "sessions" / str(app_id))

            shot = payload.screenshots_dir / f"app_{payload.job_id}_{int(datetime.now(UTC).timestamp())}.png"
            form_page.screenshot(path=str(shot), full_page=False)
            screenshot_path = str(shot)
        except Exception as e:
            logger.warning("Apply prepare failed for job %s: %s", payload.job_id, e)
            warnings.append(f"Browser automation error: {e}")
        finally:
            if not payload.headless:
                try:
                    form_page.bring_to_front()
                except Exception:
                    pass
                warnings.append(
                    "Review the application in the browser. When you are done, close the browser "
                    "window — Prepare form will finish after you close it."
                )
                try:
                    form_page.wait_for_event("close", timeout=0)
                except Exception:
                    logger.debug("Headed browser close wait ended", exc_info=True)
            context.close()

    if not fields_filled:
        warnings.append(
            "No form fields were auto-filled. Complete any remaining fields in the open browser window."
        )

    return ApplyPrepareResult(
        page_url=page_url,
        fields_filled=fields_filled,
        screenshot_path=screenshot_path,
        submit_selector=submit_selector,
        apply_flow=apply_flow,
        session_dir=session_dir,
        warnings=warnings,
        browser_available=True,
        generated_account_password=(
            payload.account_password if "ats_account_password" in fields_filled else None
        ),
    )


def _confirm_application_submit_sync(
    *,
    page_url: str,
    submit_selector: str,
    browser_dir: Path,
    headless: bool,
) -> tuple[bool, str | None]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "Playwright not installed; submission was not automated."

    browser_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(browser_dir),
            headless=headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(page_url, wait_until="domcontentloaded", timeout=45_000)
            btn = page.query_selector(submit_selector)
            if btn is None:
                return False, "Submit button no longer found on the page."
            btn.click()
            _wait_dom_settle(page)
            return True, None
        except Exception as e:
            return False, f"Could not click submit: {e}"
        finally:
            context.close()


async def prepare_application_form(
    user: User,
    job: Job,
    *,
    resume_doc: Document,
    cover_doc: Document | None,
    browser_dir: Path,
    screenshots_dir: Path,
    headless: bool = False,
    application_id: int = 0,
    prior_apply_meta: dict[str, Any] | None = None,
) -> ApplyPrepareResult:
    """Navigate to the posting, start apply, fill what we can; never click submit."""

    settings = get_settings()
    payload = _PrepareFormInput(
        user_name=user.name,
        user_email=user.email,
        user_links=dict(user.links or {}),
        years_experience=user.years_experience,
        application_defaults=merge_application_defaults(user.application_defaults, location=user.location),
        saved_application_answers=saved_answers(user.application_defaults),
        prior_apply_meta=prior_apply_meta,
        job_id=job.id,
        application_id=application_id,
        job_url=job.url,
        resume_file_path=resume_doc.file_path,
        resume_content=resume_doc.content,
        cover_content=cover_doc.content if cover_doc is not None else None,
        account_password=str(
            (prior_apply_meta or {}).get("generated_account_password")
            or _generate_account_password()
        ),
        browser_dir=browser_dir,
        screenshots_dir=screenshots_dir,
        headless=headless,
        vision_enabled=settings.apply_vision_enabled,
    )
    if settings.use_browser_use_apply():
        try:
            browser_use_result = await run_browser_use_apply(_browser_use_input(payload))
            return ApplyPrepareResult(
                page_url=browser_use_result.page_url,
                fields_filled=browser_use_result.fields_filled,
                screenshot_path=None,
                submit_selector=browser_use_result.submit_selector,
                apply_flow="browser_use",
                session_dir=None,
                warnings=browser_use_result.warnings,
                browser_available=True,
                generated_account_password=browser_use_result.generated_account_password,
                browser_engine="browser_use",
                browser_use_session_id=browser_use_result.browser_use_session_id,
                browser_use_live_url=browser_use_result.browser_use_live_url,
                browser_use_output=browser_use_result.browser_use_output,
                browser_use_mode=browser_use_result.browser_use_mode,
            )
        except Exception as e:
            logger.warning("Browser Use apply failed for job %s: %s", job.id, e)
            return ApplyPrepareResult(
                page_url=job.url,
                apply_flow="browser_use",
                warnings=[f"Browser Use apply automation failed: {e}"],
                browser_available=False,
                browser_engine="browser_use",
                browser_use_mode=(
                    "cloud" if settings.use_browser_use_cloud_apply() else "local"
                ),
            )
    return await run_playwright_in_thread(_prepare_application_form_sync, payload)


async def confirm_application_submit(
    *,
    page_url: str,
    submit_selector: str | None,
    browser_dir: Path,
    headless: bool = False,
    browser_use_session_id: str | None = None,
) -> tuple[bool, str | None]:
    """Click submit after explicit user confirmation. Returns (clicked, warning)."""

    if browser_use_session_id:
        return await confirm_browser_use_submit(browser_use_session_id)

    if not submit_selector:
        return False, "No submit button was detected during prepare; mark submitted after you apply manually."

    return await run_playwright_in_thread(
        _confirm_application_submit_sync,
        page_url=page_url,
        submit_selector=submit_selector,
        browser_dir=browser_dir,
        headless=headless,
    )
