"""Unit tests for deterministic location/work-mode fit adjustments."""

from __future__ import annotations

from app.agents.match_fit import apply_fit_adjustments, location_fit, work_mode_fit
from app.models import Job, User


def _user(**kwargs) -> User:
    user = User(name="Test")
    user.target_locations = kwargs.pop("target_locations", [])
    user.location = kwargs.pop("location", None)
    user.work_modes = kwargs.pop("work_modes", [])
    return user


def _job(**kwargs) -> Job:
    return Job(
        source="remoteok",
        external_id="x",
        url="https://example.com/job",
        title=kwargs.get("title", "Engineer"),
        company="Co",
        location=kwargs.get("location", "Remote"),
        description=kwargs.get("description", ""),
    )


def test_remote_job_boosts_when_user_wants_remote() -> None:
    user = _user(target_locations=["Remote"], work_modes=["remote"])
    job = _job(location="Remote", description="Fully distributed team.")
    delta, note = location_fit(user, job)
    assert delta > 0
    assert "Remote" in note


def test_onsite_mismatch_penalized() -> None:
    user = _user(location="Toronto, ON", target_locations=["Toronto"], work_modes=["onsite"])
    job = _job(location="Berlin, Germany", description="On-site in our Berlin office.")
    delta, note = location_fit(user, job)
    assert delta < 0
    assert "does not match" in note


def test_work_mode_remote_only_when_user_wants_onsite() -> None:
    user = _user(work_modes=["onsite"])
    job = _job(location="Remote", description="Work from anywhere.")
    delta, note = work_mode_fit(user, job)
    assert delta < 0
    assert "remote" in note.lower()


def test_apply_fit_clamps_score() -> None:
    user = _user(location="Toronto", target_locations=["Toronto"], work_modes=["onsite"])
    job = _job(location="Berlin", description="Office in Berlin.")
    score, reason = apply_fit_adjustments(user, job, 85.0, "Strong skills match.")
    assert score < 85.0
    assert "Strong skills" in reason


def test_heuristic_score_boosts_skill_overlap() -> None:
    from app.agents.match_agent import score_job_heuristic

    user = User(name="Lin")
    user.target_roles = ["Backend engineer"]
    user.skills = ["python", "fastapi"]
    user.work_modes = ["remote"]
    job = _job(
        title="Backend Engineer",
        location="Remote",
        description="Python and FastAPI microservices.",
    )
    result = score_job_heuristic(job, user)
    assert result.score >= 50
    assert "python" in result.reason.lower() or "Matched" in result.reason
