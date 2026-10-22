"""User preferences for the autonomous pipeline, stored in `users.preferences` (JSON)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..models import User


@dataclass
class Preferences:
    jobs_per_day: int = 5
    min_match_score: int = 60
    excluded_companies: list[str] = field(default_factory=list)
    discovery_time: str = "09:00"
    max_job_age_days: int = 30
    web_search_enabled: bool = True
    max_screens_per_run: int = 30
    document_style: str = "modern"
    # What the apply agent may do on the user's behalf.
    agent_accounts: bool = True  # create and sign in to job-site accounts
    agent_consents: bool = True  # accept terms, sign attestations with the user's name
    skip_reasons: dict[str, int] = field(default_factory=dict)

    def excludes(self, company: str | None) -> bool:
        name = (company or "").strip().lower()
        return bool(name) and any(name == c.strip().lower() for c in self.excluded_companies)


def load_preferences(user: User) -> Preferences:
    raw = dict(user.preferences or {})
    known = {k: v for k, v in raw.items() if k in Preferences.__dataclass_fields__}
    return Preferences(**known)


def save_preferences(user: User, prefs: Preferences) -> None:
    user.preferences = {**(user.preferences or {}), **asdict(prefs)}
