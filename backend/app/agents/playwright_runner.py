"""Run Playwright sync API outside uvicorn's asyncio loop."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")


def run_playwright_sync(func: Callable[..., T], /, *args, **kwargs) -> T:
    """Execute sync Playwright in a fresh OS thread (no running asyncio loop)."""

    outcome: dict[str, object] = {}

    def _target() -> None:
        try:
            outcome["result"] = func(*args, **kwargs)
        except BaseException as exc:
            outcome["exc"] = exc

    thread = threading.Thread(target=_target, name="jobbee-playwright")
    thread.start()
    thread.join()
    if "exc" in outcome:
        raise outcome["exc"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]


async def run_playwright_in_thread(func: Callable[..., T], /, *args, **kwargs) -> T:
    return await asyncio.to_thread(run_playwright_sync, func, *args, **kwargs)


def run_coro_sync(coro: Coroutine[Any, Any, T]) -> T:
    """Run an async coroutine from sync code, even inside Playwright's event loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    outcome: dict[str, object] = {}

    def _target() -> None:
        try:
            outcome["result"] = asyncio.run(coro)
        except BaseException as exc:
            outcome["exc"] = exc

    thread = threading.Thread(target=_target, name="jobbee-async-coro")
    thread.start()
    thread.join()
    if "exc" in outcome:
        raise outcome["exc"]  # type: ignore[misc]
    return outcome["result"]  # type: ignore[return-value]
