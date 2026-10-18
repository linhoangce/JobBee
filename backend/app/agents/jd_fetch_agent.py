"""Fetch full job descriptions from posting URLs via Playwright."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from .playwright_runner import run_playwright_in_thread

logger = logging.getLogger(__name__)

_MAX_JD_CHARS = 6_000

_LOGIN_URL_HINTS = ("login", "authwall", "checkpoint", "signin", "sign-in", "uas/")
_LOGIN_TEXT_HINTS = (
    "sign in to linkedin",
    "join linkedin",
    "log in to continue",
    "sign in to view",
    "create an account",
    "verify your identity",
    "security verification",
)

_WHITESPACE_RE = re.compile(r"\s+")


@dataclass(slots=True)
class JdFetchResult:
    page_url: str
    text: str | None = None
    warnings: list[str] = field(default_factory=list)
    browser_available: bool = True
    login_required: bool = False


@dataclass(slots=True)
class _FetchJdInput:
    job_url: str
    browser_dir: Path
    headless: bool
    max_chars: int


def normalize_jd_text(raw: str) -> str:
    """Collapse whitespace and trim extracted page text."""

    return _WHITESPACE_RE.sub(" ", raw).strip()


def looks_like_login_wall(page_url: str, body_text: str) -> bool:
    """Heuristic: redirect to auth or page body dominated by sign-in copy."""

    url_low = page_url.lower()
    if any(hint in url_low for hint in _LOGIN_URL_HINTS):
        return True

    text_low = body_text.lower()[:2_000]
    matches = sum(1 for hint in _LOGIN_TEXT_HINTS if hint in text_low)
    return matches >= 2


def _extract_page_text(page) -> str:
    try:
        return page.evaluate(
            """() => {
                const main = document.querySelector(
                    'main, [role="main"], article, .jobs-description, .job-description, #job-details'
                );
                const root = main || document.body;
                return root ? root.innerText : '';
            }"""
        )
    except Exception:
        body = page.query_selector("body")
        if body is None:
            return ""
        return body.inner_text()


def _fetch_jd_sync(payload: _FetchJdInput) -> JdFetchResult:
    """Run Playwright in a worker thread (Windows uvicorn cannot spawn subprocesses on the main loop)."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return JdFetchResult(
            page_url=payload.job_url,
            browser_available=False,
            warnings=[
                "Playwright is not installed. Run: pip install playwright && playwright install chromium",
            ],
        )

    payload.browser_dir.mkdir(parents=True, exist_ok=True)
    warnings: list[str] = []
    page_url = payload.job_url
    text: str | None = None
    login_required = False

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(payload.browser_dir),
            headless=payload.headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        try:
            page.goto(payload.job_url, wait_until="domcontentloaded", timeout=45_000)
            page_url = page.url

            raw_text = normalize_jd_text(_extract_page_text(page))
            if not raw_text:
                warnings.append("Could not extract text from the job posting page.")
                return JdFetchResult(
                    page_url=page_url,
                    warnings=warnings,
                    browser_available=True,
                )

            if looks_like_login_wall(page_url, raw_text):
                login_required = True
                warnings.append(
                    "Job description fetch skipped: login or verification wall detected. "
                    "Log in once via the apply browser profile, then retry sync."
                )
                return JdFetchResult(
                    page_url=page_url,
                    warnings=warnings,
                    browser_available=True,
                    login_required=True,
                )

            if len(raw_text) < 80:
                warnings.append("Extracted job description was very short; page may not have loaded fully.")

            text = raw_text[: payload.max_chars]
        except Exception as e:
            logger.warning("JD fetch failed for %s: %s", payload.job_url, e)
            warnings.append(f"Browser automation error while fetching job description: {e}")
        finally:
            context.close()

    return JdFetchResult(
        page_url=page_url,
        text=text,
        warnings=warnings,
        browser_available=True,
        login_required=login_required,
    )


async def fetch_job_description(
    job_url: str,
    *,
    browser_dir: Path,
    headless: bool = True,
    max_chars: int = _MAX_JD_CHARS,
) -> JdFetchResult:
    """Open the posting in the shared persistent browser profile and return plain-text JD."""

    payload = _FetchJdInput(
        job_url=job_url,
        browser_dir=browser_dir,
        headless=headless,
        max_chars=max_chars,
    )
    return await run_playwright_in_thread(_fetch_jd_sync, payload)
