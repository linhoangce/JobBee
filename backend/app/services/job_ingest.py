"""Shared helpers to persist listings and link them to the user's applications."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, ApplicationStatus, Job, User


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
