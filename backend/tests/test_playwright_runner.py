"""Playwright thread runner tests."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.agents.playwright_runner import run_coro_sync, run_playwright_in_thread, run_playwright_sync


def test_run_playwright_sync_runs_in_fresh_thread() -> None:
    import threading

    seen: dict[str, int] = {}

    def worker() -> int:
        seen["thread"] = threading.get_ident()
        return 42

    seen["caller"] = threading.get_ident()
    assert run_playwright_sync(worker) == 42
    assert seen["thread"] != seen["caller"]


@pytest.mark.asyncio
async def test_run_coro_sync_works_inside_running_loop() -> None:
    async def _answer() -> str:
        return "vision-ok"

    assert await asyncio.to_thread(run_coro_sync, _answer()) == "vision-ok"


@pytest.mark.asyncio
async def test_run_playwright_in_thread_delegates() -> None:
    with patch(
        "app.agents.playwright_runner.run_playwright_sync",
        return_value="ok",
    ) as run_sync:
        result = await run_playwright_in_thread(lambda: "ignored")

    assert result == "ok"
    run_sync.assert_called_once()
