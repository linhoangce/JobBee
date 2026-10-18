"""Deterministic location / work-mode fit adjustments on top of LLM scores."""

from __future__ import annotations

from ..models import Job, User

_REMOTE_TOKENS = frozenset(
    {"remote", "worldwide", "anywhere", "wfh", "work from home", "distributed", "global"}
)


def _norm(text: str | None) -> str:
    return (text or "").lower().strip()


def _user_location_targets(user: User) -> list[str]:
    targets: list[str] = []
    for raw in [*(user.target_locations or []), user.location or ""]:
        t = _norm(raw)
        if not t or t in {"any", "not specified", "n/a", "none"}:
            continue
        targets.append(t)
    return targets


def location_fit(user: User, job: Job) -> tuple[float, str]:
    """Return a score delta (-30..+15) and a short note."""

    targets = _user_location_targets(user)
    if not targets:
        return 0.0, ""

    job_loc = _norm(job.location)
    job_text = _norm((job.description or "")[:600])
    blob = f"{job_loc} {job_text}"

    is_remote_job = job_loc == "remote" or any(tok in blob for tok in _REMOTE_TOKENS)

    if is_remote_job:
        if any("remote" in t for t in targets):
            return 12.0, "Remote role fits your location preferences."
        if user.work_modes and "remote" not in user.work_modes:
            return -18.0, "Remote-only role; your profile prefers on-site/hybrid in a specific area."
        return 3.0, "Remote role (verify timezone/region fit)."

    for target in targets:
        if len(target) < 3:
            continue
        # Match city/region/country substring in job location or description header.
        if target in job_loc or target in job_text[:400]:
            return 15.0, f"Location aligns with {target.title()}."

    return -20.0, "Job location does not match your target areas."


def work_mode_fit(user: User, job: Job) -> tuple[float, str]:
    """Return a score delta (-20..+10) and a short note."""

    if not (user.work_modes or []):
        return 0.0, ""

    blob = _norm(f"{job.location} {job.title} {(job.description or '')[:800]}")
    is_remote = "remote" in blob or any(tok in blob for tok in _REMOTE_TOKENS)

    wants_remote = "remote" in user.work_modes
    wants_hybrid = "hybrid" in user.work_modes
    wants_onsite = "onsite" in user.work_modes

    if is_remote and wants_remote:
        return 8.0, "Matches your remote work preference."
    if is_remote and not wants_remote and (wants_onsite or wants_hybrid):
        return -15.0, "Remote listing; you prefer on-site or hybrid."
    if not is_remote and wants_onsite and not wants_remote:
        return 5.0, "On-site/hybrid style listing."
    return 0.0, ""


def apply_fit_adjustments(user: User, job: Job, base_score: float, base_reason: str) -> tuple[float, str]:
    loc_delta, loc_note = location_fit(user, job)
    mode_delta, mode_note = work_mode_fit(user, job)
    final = max(0.0, min(100.0, base_score + loc_delta + mode_delta))
    parts = [base_reason]
    if loc_note:
        parts.append(loc_note)
    if mode_note:
        parts.append(mode_note)
    return final, " ".join(p for p in parts if p)[:500]
