"""What the agent looks for and how many jobs it sends (the welcome form and Settings).

GET /api/preferences
PUT /api/preferences      partial update
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_session
from ..documents.render_pdf import STYLES, get_style
from ..models import User
from ..pipeline.prefs import load_preferences, save_preferences
from ..schemas import PreferencesPayload
from ..services.application_defaults import merge_application_defaults
from .profile import get_or_create_local_user

router = APIRouter(prefix="/preferences", tags=["preferences"])

_PROFILE_FIELDS = ("target_roles", "target_locations", "work_modes", "min_salary")
_DEFAULTS_FIELDS = ("work_authorization", "requires_sponsorship")
_PREF_FIELDS = (
    "jobs_per_day",
    "min_match_score",
    "excluded_companies",
    "discovery_time",
    "max_job_age_days",
    "web_search_enabled",
    "document_style",
    "agent_accounts",
    "agent_consents",
)


def _clean_list(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        value = " ".join(value.split())
        if value and value.lower() not in {v.lower() for v in out}:
            out.append(value[:120])
    return out[:20]


def _read(user: User) -> PreferencesPayload:
    prefs = load_preferences(user)
    defaults = merge_application_defaults(user.application_defaults, location=user.location)
    return PreferencesPayload(
        target_roles=list(user.target_roles or []),
        target_locations=list(user.target_locations or []),
        work_modes=list(user.work_modes or []),
        min_salary=user.min_salary,
        work_authorization=defaults.get("work_authorization"),
        requires_sponsorship=defaults.get("requires_sponsorship"),
        jobs_per_day=prefs.jobs_per_day,
        min_match_score=prefs.min_match_score,
        excluded_companies=prefs.excluded_companies,
        discovery_time=prefs.discovery_time,
        max_job_age_days=prefs.max_job_age_days,
        web_search_enabled=prefs.web_search_enabled,
        document_style=get_style(prefs.document_style).id,
        agent_accounts=prefs.agent_accounts,
        agent_consents=prefs.agent_consents,
    )


@router.get("", response_model=PreferencesPayload)
def read_preferences(session: Session = Depends(get_session)) -> PreferencesPayload:
    return _read(get_or_create_local_user(session))


@router.put("", response_model=PreferencesPayload)
def update_preferences(
    payload: PreferencesPayload, session: Session = Depends(get_session)
) -> PreferencesPayload:
    user = get_or_create_local_user(session)
    data = payload.model_dump(exclude_unset=True)
    if data.get("document_style") is not None and data["document_style"] not in STYLES:
        raise HTTPException(status_code=400, detail="Unknown CV style.")

    for key in _PROFILE_FIELDS:
        if key in data:
            value = data[key]
            if key in ("target_roles", "target_locations"):
                value = _clean_list(value or [])
            elif key == "work_modes":
                value = list(dict.fromkeys(value or []))
            setattr(user, key, value)

    if any(key in data for key in _DEFAULTS_FIELDS):
        defaults = dict(user.application_defaults or {})
        for key in _DEFAULTS_FIELDS:
            if key in data:
                if data[key] is None:
                    defaults.pop(key, None)
                else:
                    defaults[key] = data[key]
        user.application_defaults = defaults

    prefs = load_preferences(user)
    for key in _PREF_FIELDS:
        if key in data and data[key] is not None:
            value = data[key]
            if key == "excluded_companies":
                value = _clean_list(value)
            setattr(prefs, key, value)
    save_preferences(user, prefs)

    session.commit()
    session.refresh(user)
    return _read(user)
