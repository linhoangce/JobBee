"""Claude spend ledger and budget gate.

Every Claude call records its tokens and estimated cost in `llm_usage`. Before a
call, `check_budget()` refuses to spend once today's or this month's total
passes the configured budget, or while the agent is paused for budget reasons.
Call sites label their work with `llm_task("match")` so spend can be broken down.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from ..apply.costs import usage_cost
from ..config import get_settings
from ..db import SessionLocal
from ..models import LlmUsage
from .base import LLMError

WEB_SEARCH_USD = 0.01  # $10 per 1,000 searches

_current_task: ContextVar[str] = ContextVar("llm_task", default="other")


class BudgetExceededError(LLMError):
    """Spending more would pass the daily or monthly Claude budget."""


@contextmanager
def llm_task(name: str) -> Iterator[None]:
    token = _current_task.set(name)
    try:
        yield
    finally:
        _current_task.reset(token)


def current_task() -> str:
    return _current_task.get()


def record_usage(
    model: str,
    usage: Any,
    *,
    task: str | None = None,
    application_id: int | None = None,
    run_id: int | None = None,
) -> float:
    """Store one response's usage; returns its estimated cost in USD."""

    server_tools = getattr(usage, "server_tool_use", None)
    searches = getattr(server_tools, "web_search_requests", 0) or 0
    cost = usage_cost(model, usage) + searches * WEB_SEARCH_USD
    with SessionLocal() as session:
        session.add(
            LlmUsage(
                task=task or current_task(),
                model=model,
                input_tokens=getattr(usage, "input_tokens", 0) or 0,
                output_tokens=getattr(usage, "output_tokens", 0) or 0,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                web_searches=searches,
                cost_usd=cost,
                application_id=application_id,
                run_id=run_id,
            )
        )
        session.commit()
    return cost


def spend_since(since: datetime) -> float:
    with SessionLocal() as session:
        total = session.execute(
            select(func.coalesce(func.sum(LlmUsage.cost_usd), 0.0)).where(
                LlmUsage.created_at >= since
            )
        ).scalar_one()
    return float(total or 0.0)


def _day_start(now: datetime) -> datetime:
    local = now.astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def _month_start(now: datetime) -> datetime:
    local = now.astimezone()
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def spend_summary(now: datetime | None = None) -> dict[str, float]:
    now = now or datetime.now(timezone.utc)
    settings = get_settings()
    return {
        "today_usd": spend_since(_day_start(now)),
        "month_usd": spend_since(_month_start(now)),
        "last_7_days_usd": spend_since(now - timedelta(days=7)),
        "daily_budget_usd": settings.daily_budget_usd,
        "monthly_budget_usd": settings.monthly_budget_usd,
    }


def check_budget() -> None:
    """Raise BudgetExceededError if today's or this month's spend is at its limit."""

    summary = spend_summary()
    if summary["daily_budget_usd"] and summary["today_usd"] >= summary["daily_budget_usd"]:
        raise BudgetExceededError(
            f"Today's Claude budget (${summary['daily_budget_usd']:.2f}) is used up."
        )
    if summary["monthly_budget_usd"] and summary["month_usd"] >= summary["monthly_budget_usd"]:
        raise BudgetExceededError(
            f"This month's Claude budget (${summary['monthly_budget_usd']:.2f}) is used up."
        )
