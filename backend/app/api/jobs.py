"""Job search + feed endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.search_agent import run_search
from ..agents.sources.registry import AVAILABLE_SOURCES, BLOCKED_SOURCES, EMAIL_SOURCES
from ..api.profile import get_or_create_local_user
from ..db import get_session
from ..models import Application, Job
from ..schemas import JobResponse, JobSearchRequest, JobSearchResponse
from ..services.approvals import queue_top_matches, resolve_top_n

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_to_response(job: Job, application_status: str | None = None) -> JobResponse:
    return JobResponse(
        id=job.id,
        source=job.source,
        external_id=job.external_id,
        url=job.url,
        title=job.title,
        company=job.company,
        location=job.location,
        description=job.description,
        match_score=job.match_score,
        match_reason=job.match_reason,
        created_at=job.created_at,
        application_status=application_status,
    )


@router.get("/sources")
def list_sources() -> dict:
    return {
        "available": sorted(AVAILABLE_SOURCES.keys()) + sorted(EMAIL_SOURCES),
        "blocked": sorted(BLOCKED_SOURCES),
        "notes": {
            "linkedin": (
                "Automated LinkedIn search is disabled to protect your account. "
                "LinkedIn prohibits scraping/automation and aggressively bans bots."
            ),
            "email_linkedin": (
                "Ingest LinkedIn job-alert emails from your connected inbox. "
                "Connect Gmail or IMAP in Settings, then sync from the Jobs page."
            ),
            "recommended": "Connect Gmail in Settings, then sync LinkedIn job-alert emails from the Jobs page.",
        },
    }


@router.get("", response_model=list[JobResponse])
def list_jobs(
    min_score: float | None = None,
    session: Session = Depends(get_session),
) -> list[JobResponse]:
    user = get_or_create_local_user(session)
    stmt = (
        select(Job, Application.status)
        .join(Application, Application.job_id == Job.id)
        .where(Application.user_id == user.id)
        .order_by(Job.match_score.desc().nulls_last(), Job.created_at.desc())
    )
    if min_score is not None:
        stmt = stmt.where(Job.match_score >= min_score)

    rows = session.execute(stmt).all()
    return [
        _job_to_response(job, application_status=status.value if hasattr(status, "value") else str(status))
        for job, status in rows
    ]


@router.post("/search", response_model=JobSearchResponse)
async def search_jobs(
    payload: JobSearchRequest,
    session: Session = Depends(get_session),
) -> JobSearchResponse:
    user = get_or_create_local_user(session)
    if not user.target_roles and not user.skills:
        raise HTTPException(
            status_code=400,
            detail="Complete onboarding first — we need target roles or skills to search.",
        )

    # Run the async agent; FastAPI endpoint is async so we await directly.
    result = await run_search(
        session,
        user,
        sources=payload.sources,
        limit_per_source=payload.limit_per_source,
        score_jobs=payload.score,
    )

    approval_queued = 0
    if payload.queue_for_approval and payload.score:
        top_n = resolve_top_n(user, payload.approval_top_n)
        approval_queued = queue_top_matches(session, user, top_n)
        session.commit()

    jobs = list_jobs(min_score=None, session=session)
    return JobSearchResponse(
        sources_queried=result.sources_queried,
        jobs_found=result.jobs_found,
        jobs_upserted=result.jobs_upserted,
        jobs_scored=result.jobs_scored,
        approval_queued=approval_queued,
        warnings=result.warnings,
        jobs=jobs,
    )
