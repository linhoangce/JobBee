"""Application pipeline: tailor docs, prepare apply, confirm submit."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..agents.apply_agent import decode_apply_meta
from ..llm.base import LLMError
from ..api.jobs import _job_to_response
from ..api.profile import get_or_create_local_user
from ..db import get_session
from ..models import Application, ApplicationStatus, Document
from ..schemas import (
    ApplicationDetailResponse,
    ApplicationListResponse,
    ApplicationSummary,
    ConfirmSubmitResponse,
    PrepareApplyResponse,
    TailorApplicationResponse,
)
from ..services.apply import (
    SCREENSHOTS_DIR,
    confirm_submit,
    get_application,
    list_applications,
    mark_failed,
    prepare_apply,
)
from ..services.tailoring import tailor_application

router = APIRouter(prefix="/applications", tags=["applications"])


def _doc_preview(session: Session, doc_id: int | None) -> str | None:
    if doc_id is None:
        return None
    doc = session.get(Document, doc_id)
    if doc is None or not doc.content:
        return None
    text = doc.content.strip()
    return text[:500] + ("…" if len(text) > 500 else "")


def _application_to_response(session: Session, app: Application) -> ApplicationDetailResponse:
    status = app.status.value if hasattr(app.status, "value") else str(app.status)
    meta = decode_apply_meta(app.notes)
    return ApplicationDetailResponse(
        id=app.id,
        status=status,  # type: ignore[arg-type]
        job=_job_to_response(app.job, application_status=status),
        notes=app.notes if meta is None else None,
        resume_doc_id=app.resume_doc_id,
        cover_doc_id=app.cover_doc_id,
        submitted_at=app.submitted_at,
        resume_preview=_doc_preview(session, app.resume_doc_id),
        cover_preview=_doc_preview(session, app.cover_doc_id),
        apply_prepared=meta is not None,
        apply_meta=meta,
        created_at=app.created_at,
        updated_at=app.updated_at,
    )


def _summary(session: Session, user_id: int) -> ApplicationSummary:
    rows = session.execute(
        select(Application.status, func.count())
        .where(Application.user_id == user_id)
        .group_by(Application.status)
    ).all()
    counts = {status: count for status, count in rows}
    return ApplicationSummary(
        approved=counts.get(ApplicationStatus.approved, 0),
        submitted=counts.get(ApplicationStatus.submitted, 0),
        failed=counts.get(ApplicationStatus.failed, 0),
    )


@router.get("", response_model=ApplicationListResponse)
def list_pipeline(
    status: ApplicationStatus | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ApplicationListResponse:
    user = get_or_create_local_user(session)
    apps = list_applications(session, user, status=status)
    return ApplicationListResponse(
        summary=_summary(session, user.id),
        items=[_application_to_response(session, app) for app in apps],
    )


@router.get("/{application_id}", response_model=ApplicationDetailResponse)
def get_pipeline_application(
    application_id: int,
    session: Session = Depends(get_session),
) -> ApplicationDetailResponse:
    user = get_or_create_local_user(session)
    app = get_application(session, user, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    return _application_to_response(session, app)


@router.post("/{application_id}/tailor", response_model=TailorApplicationResponse)
async def tailor(
    application_id: int,
    session: Session = Depends(get_session),
) -> TailorApplicationResponse:
    user = get_or_create_local_user(session)
    try:
        app, warnings = await tailor_application(session, user, application_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    detail = _application_to_response(session, app)
    return TailorApplicationResponse(application=detail, warnings=warnings)


@router.post("/{application_id}/prepare-apply", response_model=PrepareApplyResponse)
async def prepare_application(
    application_id: int,
    session: Session = Depends(get_session),
) -> PrepareApplyResponse:
    user = get_or_create_local_user(session)
    try:
        app, warnings = await prepare_apply(session, user, application_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    detail = _application_to_response(session, app)
    return PrepareApplyResponse(application=detail, warnings=warnings)


@router.post("/{application_id}/confirm-submit", response_model=ConfirmSubmitResponse)
async def confirm_application(
    application_id: int,
    session: Session = Depends(get_session),
) -> ConfirmSubmitResponse:
    user = get_or_create_local_user(session)
    try:
        app, warnings = await confirm_submit(session, user, application_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    detail = _application_to_response(session, app)
    return ConfirmSubmitResponse(application=detail, warnings=warnings)


@router.post("/{application_id}/mark-failed", response_model=ApplicationDetailResponse)
def fail_application(
    application_id: int,
    reason: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> ApplicationDetailResponse:
    user = get_or_create_local_user(session)
    try:
        app = mark_failed(session, user, application_id, reason=reason)
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    session.commit()
    session.refresh(app)
    return _application_to_response(session, app)


@router.get("/{application_id}/screenshot")
def get_screenshot(
    application_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    user = get_or_create_local_user(session)
    app = get_application(session, user, application_id)
    if app is None:
        raise HTTPException(status_code=404, detail="Application not found.")
    meta = decode_apply_meta(app.notes)
    if meta is None or not meta.get("screenshot_path"):
        raise HTTPException(status_code=404, detail="No screenshot for this application.")
    path = Path(str(meta["screenshot_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Screenshot file missing.")
    try:
        path.resolve().relative_to(SCREENSHOTS_DIR.resolve())
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid screenshot path.") from e
    return FileResponse(path, media_type="image/png")
