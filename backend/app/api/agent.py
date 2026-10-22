"""Tell the agent what to do: search now, prepare more, pause/resume, read a link.

POST /api/agent/search            find jobs now (background)
POST /api/agent/more              prepare cards for the next shortlisted jobs
POST /api/agent/pause | resume
POST /api/agent/notices/{id}/dismiss | retry
POST /api/jobs/link               read a posting the user pasted, then screen + tailor it
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..apply.runs import run_manager
from ..db import get_session
from ..models import DocumentKind, User
from ..pipeline.discovery import discovery
from ..pipeline.flow import job_flow
from ..pipeline.ingest import IngestError
from ..pipeline.state import set_paused
from ..schemas import ActionResponse, JobLinkRequest, JobLinkResponse
from ..services.documents import get_base_document
from ..services.secrets import claude_api_key
from .profile import get_or_create_local_user

router = APIRouter(tags=["agent"])


class MoreRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10)


def require_ready(session: Session, user: User) -> None:
    """Searching and tailoring need a Claude key and a resume."""

    if not claude_api_key():
        raise HTTPException(status_code=400, detail="Connect Claude first (Settings → Claude).")
    resume = get_base_document(session, user.id, DocumentKind.resume)
    if resume is None or not (resume.content or "").strip():
        raise HTTPException(status_code=400, detail="Upload your resume first.")


@router.post("/agent/search", response_model=ActionResponse, status_code=status.HTTP_202_ACCEPTED)
async def search_now(session: Session = Depends(get_session)) -> ActionResponse:
    user = get_or_create_local_user(session)
    require_ready(session, user)
    if not discovery.start(user.id, "manual"):
        raise HTTPException(status_code=409, detail="A search is already running.")
    return ActionResponse(message="Searching for jobs…")


@router.post("/agent/more", response_model=ActionResponse, status_code=status.HTTP_202_ACCEPTED)
async def prepare_more(
    payload: MoreRequest | None = None, session: Session = Depends(get_session)
) -> ActionResponse:
    user = get_or_create_local_user(session)
    require_ready(session, user)
    count = (payload or MoreRequest()).count
    if not discovery.start_more(user.id, count):
        raise HTTPException(status_code=409, detail="A search is already running.")
    return ActionResponse(message=f"Preparing {count} more…")


@router.post("/agent/pause", response_model=ActionResponse)
def pause() -> ActionResponse:
    set_paused(True)
    return ActionResponse(message="Paused. Nothing new starts until you resume.")


@router.post("/agent/resume", response_model=ActionResponse)
async def resume() -> ActionResponse:
    set_paused(False)
    run_manager.kick()
    return ActionResponse(message="Back on.")


@router.post("/agent/notices/{notice_id}/dismiss", response_model=ActionResponse)
def dismiss_notice(notice_id: int) -> ActionResponse:
    job_flow.dismiss_notice(notice_id)
    return ActionResponse()


@router.post("/agent/notices/{notice_id}/retry", response_model=ActionResponse)
async def retry_notice(notice_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    user = get_or_create_local_user(session)
    notice = next((n for n in job_flow.notices if n.id == notice_id), None)
    if notice is None or notice.user_id != user.id or notice.application_id is None:
        raise HTTPException(status_code=404, detail="Nothing to retry.")
    require_ready(session, user)
    job_flow.dismiss_notice(notice_id)
    job_flow.start(notice.application_id, user_requested=True)
    return ActionResponse(message="Trying again…")


@router.post("/jobs/link", response_model=JobLinkResponse, status_code=status.HTTP_202_ACCEPTED)
async def add_link(
    payload: JobLinkRequest, session: Session = Depends(get_session)
) -> JobLinkResponse:
    user = get_or_create_local_user(session)
    require_ready(session, user)
    try:
        result = await job_flow.add_link(user.id, payload.url, source="link")
    except IngestError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    processing = job_flow.needs_processing(result)
    if processing:
        job_flow.start(result.application_id, user_requested=True)
        message = "Got it. Checking the fit and tailoring your CV…"
    else:
        message = f"You already have this one ({result.status.value.replace('_', ' ')})."
    return JobLinkResponse(
        application_id=result.application_id,
        status=result.status.value,  # type: ignore[arg-type]
        duplicate=result.duplicate,
        processing=processing,
        message=message,
    )
