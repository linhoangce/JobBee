"""Answers the applicant gives the agent are kept with their profile and reused next time."""

from __future__ import annotations

from ..db import SessionLocal
from ..models import User
from ..services.application_defaults import fingerprint


def remember_answer(user_id: int, question: str, answer: str) -> None:
    key = fingerprint(question)
    if not key or not answer.strip():
        return
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            return
        defaults = dict(user.application_defaults or {})
        saved = dict(defaults.get("saved_answers") or {})
        saved[key] = answer.strip()
        defaults["saved_answers"] = saved
        user.application_defaults = defaults  # reassign so SQLAlchemy sees the JSON change
        session.commit()
