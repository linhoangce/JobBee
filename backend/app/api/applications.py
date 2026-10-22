"""One application: details for the drawer, and the user's decisions on it.

GET  /api/applications/{id}
POST /api/applications/{id}/apply      Apply on a match card (queues the Claude agent)
POST /api/applications/{id}/skip       {reason?}
POST /api/applications/{id}/prepare    tailor a shortlisted job now, or a card's CV again
POST /api/applications/{id}/retry      fill the form again after a failed or timed-out run
POST /api/applications/{id}/style      {style} render the CV and cover letter in another style
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..apply.runs import OPEN_STATUSES, RunConflictError, run_manager
from ..db import get_session
from ..documents.render_pdf import STYLES
from ..models import Application, ApplicationStatus, ApplyRun, Job
from ..pipeline.flow import FlowError, job_flow
from ..schemas import ActionResponse, ApplicationView, RunSummary, SkipRequest, StyleRequest
from .agent import require_ready
from .home import _aware, can_restyle, job_info, status_view
from .profile import get_or_create_local_user

router = APIRouter(prefix="/applications", tags=["applications"])

_RETRYABLE = {ApplicationStatus.failed, ApplicationStatus.approved}


def _owned(session: Session, application_id: int) -> Application:
    user = get_or_create_local_user(session)
    app = session.get(Application, application_id)
    if app is None or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


@router.get("/{application_id}", response_model=ApplicationView)
def get_application(
    application_id: int, session: Session = Depends(get_session)
) -> ApplicationView:
    app = _owned(session, application_id)
    job = session.get(Job, app.job_id)
    runs = (
        session.execute(
            select(ApplyRun).where(ApplyRun.application_id == app.id).order_by(ApplyRun.id.desc())
        )
        .scalars()
        .all()
    )
    group, label = status_view(app, runs[0].status if runs else None)
    tailoring = app.tailoring or {}
    open_run = any(r.status in OPEN_STATUSES for r in runs)
    return ApplicationView(
        id=app.id,
        status=app.status.value,  # type: ignore[arg-type]
        group=group,  # type: ignore[arg-type]
        label=label,
        stage_reason=app.stage_reason,
        job=job_info(job),
        description=job.description,
        posted_at=_aware(job.posted_at),
        discovered_via=job.discovered_via or job.source,
        screen=app.screen,
        changes=list(tailoring.get("changes") or []),
        removed_claims=list(tailoring.get("removed_claims") or []),
        resume_doc_id=app.resume_doc_id,
        cover_doc_id=app.cover_doc_id,
        style=tailoring.get("style"),
        can_restyle=can_restyle(app) and not open_run,
        runs=[
            RunSummary(
                id=r.id,
                status=r.status,
                created_at=_aware(r.created_at),
                finished_at=_aware(r.finished_at),
                cost_usd=r.cost_usd,
                iterations=r.iterations,
                error=r.error,
                summary=(r.finish or {}).get("summary"),
                attended=r.attended,
                answers=list(r.answers or (r.finish or {}).get("answers") or []),
                checks=list((r.finish or {}).get("fields_needing_review") or []),
                screenshot_url=f"/api/apply-runs/{r.id}/screenshot" if r.screenshot_path else None,
            )
            for r in runs
        ],
        can_retry=not open_run and app.status in _RETRYABLE,
        created_at=_aware(app.created_at),
        updated_at=_aware(app.updated_at),
        submitted_at=_aware(app.submitted_at),
    )


@router.post("/{application_id}/apply", response_model=ActionResponse)
async def apply(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    try:
        run_id = await job_flow.approve(app.id, app.user_id)
    except FlowError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ActionResponse(
        message="On it. You'll review the filled form before anything is submitted.",
        run_id=run_id,
    )


@router.post("/{application_id}/skip", response_model=ActionResponse)
async def skip(
    application_id: int,
    payload: SkipRequest | None = None,
    session: Session = Depends(get_session),
) -> ActionResponse:
    app = _owned(session, application_id)
    reason = payload.reason if payload else None
    try:
        message = await job_flow.skip(app.id, app.user_id, reason)
    except FlowError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ActionResponse(message=message)


@router.post(
    "/{application_id}/prepare", response_model=ActionResponse, status_code=status.HTTP_202_ACCEPTED
)
async def prepare(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    require_ready(session, app.user)
    if app.id in job_flow.stages:
        raise HTTPException(status_code=409, detail="Already working on it.")
    if app.status == ApplicationStatus.awaiting_approval and app.screen is not None:
        # A card whose CV should be tailored again, e.g. from a newer resume.
        job_flow.start_prepare(app.id)
        return ActionResponse(message="Tailoring this CV again from your current resume…")
    if app.status not in (ApplicationStatus.matched, ApplicationStatus.filtered_out):
        raise HTTPException(status_code=409, detail="This one is already prepared.")
    if app.screen is None:
        job_flow.start(app.id, user_requested=True)
    else:
        job_flow.start_prepare(app.id)
    return ActionResponse(message="Tailoring your CV…")


@router.post("/{application_id}/retry", response_model=ActionResponse)
async def retry(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    if app.status not in _RETRYABLE:
        raise HTTPException(status_code=409, detail="There's nothing to retry for this one.")
    try:
        run_id = run_manager.retry(app.id)
    except RunConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ActionResponse(message="Queued it again.", run_id=run_id)


@router.post("/{application_id}/style", response_model=ActionResponse)
async def restyle(
    application_id: int, payload: StyleRequest, session: Session = Depends(get_session)
) -> ActionResponse:
    app = _owned(session, application_id)
    if payload.style not in STYLES:
        raise HTTPException(status_code=400, detail="Unknown CV style.")
    open_run = session.execute(
        select(ApplyRun.id).where(
            ApplyRun.application_id == app.id, ApplyRun.status.in_(OPEN_STATUSES)
        )
    ).first()
    if open_run or app.id in job_flow.stages or not can_restyle(app):
        raise HTTPException(
            status_code=409, detail="This CV can't change style now (it's in use or already sent)."
        )
    name = STYLES[payload.style].name
    if (app.tailoring or {}).get("cv"):
        try:
            await job_flow.restyle(app.id, payload.style)
        except FlowError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ActionResponse(message=f"Your CV and cover letter now use the {name} style.")
    # Made before styles existed: tailor it again, in the chosen style.
    require_ready(session, app.user)
    app.tailoring = {**(app.tailoring or {}), "style": payload.style}
    session.commit()
    job_flow.start_prepare(app.id)
    return ActionResponse(
        message=f"Rewriting this CV in the {name} style. It takes about a minute."
    )
