"""Daily job search at the user's chosen time, caught up after the PC was asleep or off."""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from sqlalchemy import select

from ..db import SessionLocal
from ..models import User
from .discovery import Discovery, discovery
from .prefs import load_preferences
from .state import get_flag, is_paused, set_flag

logger = logging.getLogger(__name__)

LAST_SEARCH_KEY = "last_scheduled_search"
TICK_S = 60.0


def parse_time(value: str | None) -> time:
    try:
        hour, minute = (int(p) for p in (value or "09:00").split(":", 1))
        return time(hour % 24, minute % 60)
    except ValueError:
        return time(9, 0)


def is_due(now_local: datetime, at: time, last_day: date | None) -> bool:
    """Due once a day, from `at` onwards (so a late wake-up still catches up)."""

    return now_local.time() >= at and last_day != now_local.date()


def next_search_at(now_local: datetime, at: time, last_day: date | None) -> datetime:
    if is_due(now_local, at, last_day):
        return now_local
    today_at = datetime.combine(now_local.date(), at, tzinfo=now_local.tzinfo)
    if now_local < today_at and last_day != now_local.date():
        return today_at
    return today_at + timedelta(days=1)


def last_search_day() -> date | None:
    value = get_flag(LAST_SEARCH_KEY)
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def scheduled_user() -> User | None:
    with SessionLocal() as session:
        user = (
            session.execute(select(User).where(User.onboarded_at.is_not(None)).order_by(User.id))
            .scalars()
            .first()
        )
        if user is not None:
            session.expunge(user)
        return user


class Scheduler:
    def __init__(self, search: Discovery) -> None:
        self.search = search
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("Scheduler tick failed")
            await asyncio.sleep(TICK_S)

    def tick(self, now_local: datetime | None = None) -> bool:
        """Start today's search if it's due. Returns True when one was started."""

        now_local = now_local or datetime.now().astimezone()
        user = scheduled_user()
        if user is None or is_paused() or self.search.running:
            return False
        at = parse_time(load_preferences(user).discovery_time)
        if not is_due(now_local, at, last_search_day()):
            return False
        set_flag(LAST_SEARCH_KEY, now_local.date().isoformat())
        return self.search.start(user.id, "schedule")


scheduler = Scheduler(discovery)
