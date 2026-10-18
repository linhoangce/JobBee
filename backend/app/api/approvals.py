"""Approval queue endpoints — review top-N matches, approve or reject."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..api.jobs import _job_to_response
from ..api.profile import get_or_create_local_user
from ..db import get_session
from ..models import Application, ApplicationStatus
from ..schemas import (
    ApplicationResponse,
    ApprovalListResponse,
    ApprovalSummary,
    QueueApprovalsRequest,
    QueueApprovalsResponse,
)
from ..services.approvals import (
    approve_application,
    list_applications,
    queue_top_matches,
    reject_application,
    resolve_top_n,
)

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _application_to_response(app: Application) -> ApplicationResponse:
    status = app.status.value if hasattr(app.status, "value") else str(app.status)
    return ApplicationResponse(
        id=app.id,
        status=status,  # type: ignore[arg-type]
        job=_job_to_response(app.job, application_status=status),
        notes=app.notes,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def _summary(session: Session, user_id: int, top_n: int) -> ApprovalSummary:
    rows = session.execute(
        select(Application.status, func.count())
        .where(Application.user_id == user_id)
        .group_by(Application.status)
    ).all()
    counts = {status: count for status, count in rows}
    return ApprovalSummary(
        top_n=top_n,
        awaiting_approval=counts.get(ApplicationStatus.awaiting_approval, 0),
        approved=counts.get(ApplicationStatus.approved, 0),
        rejected=counts.get(ApplicationStatus.rejected_by_user, 0),
    )


@router.get("", response_model=ApprovalListResponse)
def list_approval_queue(session: Session = Depends(get_session)) -> ApprovalListResponse:
    user = get_or_create_local_user(session)
    top_n = resolve_top_n(user)
    pending = list_applications(session, user, status=ApplicationStatus.awaiting_approval)
    return ApprovalListResponse(
        summary=_summary(session, user.id, top_n),
        items=[_application_to_response(app) for app in pending],
    )


@router.post("/queue", response_model=QueueApprovalsResponse)
def rebuild_queue(
    payload: QueueApprovalsRequest,
    session: Session = Depends(get_session),
) -> QueueApprovalsResponse:
    user = get_or_create_local_user(session)
    top_n = resolve_top_n(user, payload.top_n)
    if payload.top_n is not None:
        user.approval_top_n = top_n
    queued = queue_top_matches(session, user, top_n)
    session.commit()
    pending = list_applications(session, user, status=ApplicationStatus.awaiting_approval)
    return QueueApprovalsResponse(
        top_n=top_n,
        queued=queued,
        items=[_application_to_response(app) for app in pending],
    )


@router.post("/{application_id}/approve", response_model=ApplicationResponse)
def approve(
    application_id: int,
    session: Session = Depends(get_session),
) -> ApplicationResponse:
    user = get_or_create_local_user(session)
    try:
        app = approve_application(session, user, application_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    return _application_to_response(app)


@router.post("/{application_id}/reject", response_model=ApplicationResponse)
def reject(
    application_id: int,
    session: Session = Depends(get_session),
) -> ApplicationResponse:
    user = get_or_create_local_user(session)
    try:
        app = reject_application(session, user, application_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    return _application_to_response(app)
