"""Small persisted agent flags (kept in the `app_config` key/value table)."""

from __future__ import annotations

from ..db import SessionLocal
from ..services.app_config import get_config_value, set_config_value

PAUSED_KEY = "agent_paused"


def get_flag(key: str) -> str | None:
    with SessionLocal() as session:
        return get_config_value(session, key)


def set_flag(key: str, value: str) -> None:
    with SessionLocal() as session:
        set_config_value(session, key, value)
        session.commit()


def is_paused() -> bool:
    return get_flag(PAUSED_KEY) == "1"


def set_paused(paused: bool) -> None:
    set_flag(PAUSED_KEY, "1" if paused else "0")
