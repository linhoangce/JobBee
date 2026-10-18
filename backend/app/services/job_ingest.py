"""Shared helpers to persist listings, link applications, and score jobs."""

from __future__ import annotations

import asyncio
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..llm.base import LLMError
from ..llm.factory import get_llm_provider
from ..models import Application, ApplicationStatus, Document, DocumentKind, Job, User
from ..agents.match_agent import score_job, score_job_heuristic

_MAX_LLM_SCORES_PER_RUN = 8
_SCORE_DELAY_S = 3.0


def _get_base_resume(session: Session, user: User) -> Document | None:
    """Pick the newest uploaded resume, skipping per-job tailored copies."""

    docs = list(
        session.execute(
            select(Document)
            .where(Document.user_id == user.id, Document.kind == DocumentKind.resume)
            .order_by(Document.id.desc())
        ).scalars()
    )
    for doc in docs:
        label = (doc.label or "").lower()
        if not label.startswith("tailored resume"):
            return doc
    return docs[0] if docs else None


class JobListingLike(Protocol):
    source: str
    external_id: str
    url: str
    title: str
    company: str | None
    location: str | None
    description: str | None


def upsert_job(session: Session, listing: JobListingLike) -> tuple[Job, bool]:
    """Return (job, created)."""

    existing = session.execute(
        select(Job).where(
            Job.source == listing.source,
            Job.external_id == listing.external_id,
        )
    ).scalar_one_or_none()

    if existing:
        existing.url = listing.url
        existing.title = listing.title
        existing.company = listing.company
        existing.location = listing.location
        if listing.description and len(listing.description) > len(existing.description or ""):
            existing.description = listing.description
        return existing, False

    job = Job(
        source=listing.source,
        external_id=listing.external_id,
        url=listing.url,
        title=listing.title,
        company=listing.company,
        location=listing.location,
        description=listing.description,
    )
    session.add(job)
    session.flush()
    return job, True


def ensure_application(session: Session, user: User, job: Job) -> None:
    exists = session.execute(
        select(Application).where(
            Application.user_id == user.id,
            Application.job_id == job.id,
        )
    ).scalar_one_or_none()
    if exists:
        return
    session.add(
        Application(
            user_id=user.id,
            job_id=job.id,
            status=ApplicationStatus.matched,
        )
    )


async def score_jobs_batch(
    session: Session,
    user: User,
    jobs: list[Job],
) -> tuple[int, list[str]]:
    """Score jobs with LLM (capped) + heuristics; return (count, warnings)."""

    if not jobs:
        return 0, []

    resume = _get_base_resume(session, user)

    provider = get_llm_provider()
    unscored = [job for job in jobs if job.match_score is None]
    rescored = [job for job in jobs if job.match_score is not None]
    llm_batch = unscored[:_MAX_LLM_SCORES_PER_RUN]
    heuristic_batch = unscored[_MAX_LLM_SCORES_PER_RUN:] + rescored
    warnings: list[str] = []
    rate_limited = False
    jobs_scored = 0

    for idx, job in enumerate(llm_batch):
        if idx > 0:
            await asyncio.sleep(_SCORE_DELAY_S)
        try:
            match = await score_job(job, user, provider=provider, resume=resume)
        except LLMError as e:
            if "429" in str(e):
                rate_limited = True
            match = score_job_heuristic(job, user)
        job.match_score = match.score
        job.match_reason = match.reason
        ensure_application(session, user, job)
        jobs_scored += 1

    for job in heuristic_batch:
        match = score_job_heuristic(job, user)
        job.match_score = match.score
        job.match_reason = match.reason
        ensure_application(session, user, job)
        jobs_scored += 1

    if rate_limited:
        warnings.append(
            "Gemini rate limit reached; some jobs used fast keyword matching instead of the LLM."
        )
    elif len(unscored) > _MAX_LLM_SCORES_PER_RUN:
        warnings.append(
            f"Scored {len(llm_batch)} new listing(s) with AI; "
            f"{len(unscored) - len(llm_batch)} others used keyword matching to stay within API limits."
        )

    return jobs_scored, warnings
