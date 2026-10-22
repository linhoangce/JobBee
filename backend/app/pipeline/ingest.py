"""Add a posting (from a link, a board, an email or web search) to the user's pipeline."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..apply.guards import url_refusal_reason
from ..db import SessionLocal
from ..models import Application, ApplicationStatus, Job, User
from ..services.job_ingest import ensure_application, upsert_job
from .jd import JobDetails, JobFetchError, fetch_job_details

_DUPLICATE_WINDOW = timedelta(days=45)


class IngestError(RuntimeError):
    pass


@dataclass
class IngestResult:
    application_id: int
    job_id: int
    status: ApplicationStatus
    duplicate: bool


def find_duplicate(session: Session, details: JobDetails) -> Job | None:
    """Same canonical URL, same content, or same company+title seen recently."""

    clauses = [Job.canonical_url == details.canonical_url]
    if details.content_hash:
        clauses.append(Job.content_hash == details.content_hash)
    existing = session.execute(select(Job).where(or_(*clauses)).limit(1)).scalar_one_or_none()
    if existing is not None or not (details.company and details.title):
        return existing
    since = datetime.now(timezone.utc) - _DUPLICATE_WINDOW
    return session.execute(
        select(Job)
        .where(
            func.lower(Job.company) == details.company.lower(),
            func.lower(Job.title) == details.title.lower(),
            Job.created_at >= since,
        )
        .limit(1)
    ).scalar_one_or_none()


def store_details(
    session: Session, user: User, details: JobDetails, *, source: str
) -> IngestResult:
    """Upsert the job + application for already-fetched details."""

    duplicate = find_duplicate(session, details)
    if duplicate is not None:
        job = duplicate
    else:
        listing = SimpleNamespace(
            source=source,
            external_id=hashlib.sha1(details.canonical_url.encode("utf-8")).hexdigest()[:20],
            url=details.url,
            title=(details.title or urlsplit(details.url).hostname or "Job posting")[:255],
            company=(details.company or None) and details.company[:255],
            location=(details.location or None) and details.location[:255],
            description=details.description,
        )
        job, _ = upsert_job(session, listing)
        job.canonical_url = details.canonical_url
        job.content_hash = details.content_hash
        job.work_mode = details.work_mode
        job.salary_text = (details.salary_text or None) and details.salary_text[:255]
        job.posted_at = details.posted_at
        job.discovered_via = source
    ensure_application(session, user, job)
    session.flush()
    app = session.execute(
        select(Application).where(Application.user_id == user.id, Application.job_id == job.id)
    ).scalar_one()
    session.commit()
    return IngestResult(
        application_id=app.id, job_id=job.id, status=app.status, duplicate=duplicate is not None
    )


async def ingest_url(user_id: int, url: str, *, source: str = "chat") -> IngestResult:
    url = url.strip()
    if urlsplit(url).scheme.lower() not in {"http", "https"} or url_refusal_reason(url):
        raise IngestError("That doesn't look like a public job posting link.")
    try:
        details = await fetch_job_details(url)
    except JobFetchError as e:
        raise IngestError(str(e)) from e
    if details.login_wall and not details.description:
        raise IngestError("That page needs a login, so I can't read the posting.")
    if not details.title and not details.description:
        raise IngestError("I couldn't find a job posting on that page.")
    with SessionLocal() as session:
        user = session.get(User, user_id)
        if user is None:
            raise IngestError("No JobBee profile yet.")
        return store_details(session, user, details, source=source)
