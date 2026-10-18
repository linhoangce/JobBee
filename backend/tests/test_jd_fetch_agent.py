"""JD fetch agent tests (Playwright mocked)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.jd_fetch_agent import (
    JdFetchResult,
    _FetchJdInput,
    _fetch_jd_sync,
    fetch_job_description,
    looks_like_login_wall,
    normalize_jd_text,
)


def test_normalize_jd_text_collapses_whitespace() -> None:
    assert normalize_jd_text("  Senior   Engineer\n\nRemote  ") == "Senior Engineer Remote"


def test_looks_like_login_wall_url() -> None:
    assert looks_like_login_wall("https://www.linkedin.com/login", "Some page")
    assert not looks_like_login_wall("https://www.linkedin.com/jobs/view/1", "Backend role at Acme.")


def test_looks_like_login_wall_body_copy() -> None:
    body = "Sign in to LinkedIn. Join LinkedIn. Log in to continue viewing this job."
    assert looks_like_login_wall("https://www.linkedin.com/jobs/view/1", body)


def _mock_playwright_page(*, url: str, inner_text: str) -> MagicMock:
    page = MagicMock()
    page.url = url
    page.evaluate.return_value = inner_text
    return page


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


def test_fetch_jd_sync_extracts_and_truncates() -> None:
    long_body = "A" * 7_000
    page = _mock_playwright_page(
        url="https://www.linkedin.com/jobs/view/123",
        inner_text=long_body,
    )
    sync_playwright = _mock_sync_playwright(page)
    payload = _FetchJdInput(
        job_url="https://www.linkedin.com/jobs/view/123",
        browser_dir=Path("/tmp/browser"),
        headless=True,
        max_chars=6_000,
    )

    with patch("playwright.sync_api.sync_playwright", sync_playwright):
        result = _fetch_jd_sync(payload)

    assert result.browser_available is True
    assert result.login_required is False
    assert result.text is not None
    assert len(result.text) == 6_000
    page.goto.assert_called_once()
    page.evaluate.assert_called_once()


def test_fetch_jd_sync_skips_login_wall() -> None:
    page = _mock_playwright_page(
        url="https://www.linkedin.com/login",
        inner_text="Sign in to LinkedIn. Join LinkedIn. Log in to continue.",
    )
    sync_playwright = _mock_sync_playwright(page)
    payload = _FetchJdInput(
        job_url="https://www.linkedin.com/jobs/view/123",
        browser_dir=Path("/tmp/browser"),
        headless=True,
        max_chars=6_000,
    )

    with patch("playwright.sync_api.sync_playwright", sync_playwright):
        result = _fetch_jd_sync(payload)

    assert result.text is None
    assert result.login_required is True
    assert any("login" in w.lower() for w in result.warnings)


def test_fetch_jd_sync_playwright_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = _FetchJdInput(
        job_url="https://example.com/jobs/1",
        browser_dir=Path("/tmp/browser"),
        headless=True,
        max_chars=6_000,
    )

    import builtins

    real_import = builtins.__import__

    def _blocked_import(name: str, *args, **kwargs):
        if name == "playwright.sync_api":
            raise ImportError("no playwright")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked_import)
    result = _fetch_jd_sync(payload)

    assert result.browser_available is False
    assert result.text is None
    assert any("Playwright is not installed" in w for w in result.warnings)


@pytest.mark.asyncio
async def test_fetch_job_description_uses_to_thread() -> None:
    expected = JdFetchResult(
        page_url="https://example.com/jobs/1",
        text="Full job description body.",
    )

    with patch(
        "app.agents.jd_fetch_agent.run_playwright_in_thread",
        new_callable=AsyncMock,
        return_value=expected,
    ) as run_thread:
        result = await fetch_job_description(
            "https://example.com/jobs/1",
            browser_dir=Path("/tmp/browser"),
        )

    assert result == expected
    run_thread.assert_awaited_once()
