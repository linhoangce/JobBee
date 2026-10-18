"""Apply workflow: prepare forms and confirm submission."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..agents.apply_agent import (
    confirm_application_submit,
    decode_apply_meta,
    encode_apply_meta,
    prepare_application_form,
)
from ..models import Application, ApplicationStatus, Document, User

BROWSER_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "browser"
SCREENSHOTS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "screenshots"


def get_application(session: Session, user: User, application_id: int) -> Application | None:
    return session.execute(
        select(Application)
        .options(joinedload(Application.job))
        .where(
            Application.id == application_id,
            Application.user_id == user.id,
        )
    ).scalar_one_or_none()


_PIPELINE_STATUSES = (
    ApplicationStatus.approved,
    ApplicationStatus.submitted,
    ApplicationStatus.failed,
)


def list_applications(
    session: Session,
    user: User,
    *,
    status: ApplicationStatus | None = None,
) -> list[Application]:
    stmt = (
        select(Application)
        .options(joinedload(Application.job))
        .where(Application.user_id == user.id)
    )
    if status is not None:
        stmt = stmt.where(Application.status == status)
    else:
        stmt = stmt.where(Application.status.in_(_PIPELINE_STATUSES))
    stmt = stmt.order_by(Application.updated_at.desc())
    return list(session.execute(stmt).scalars().unique().all())


async def prepare_apply(session: Session, user: User, application_id: int) -> tuple[Application, list[str]]:
    app = get_application(session, user, application_id)
    if app is None:
        raise LookupError("Application not found.")
    if app.status != ApplicationStatus.approved:
        raise ValueError(f"Cannot prepare apply for status {app.status.value}.")
    if app.resume_doc_id is None or app.cover_doc_id is None:
        raise ValueError("Tailor documents first before preparing the application.")

    resume_doc = session.get(Document, app.resume_doc_id)
    cover_doc = session.get(Document, app.cover_doc_id)
    if resume_doc is None:
        raise ValueError("Tailored resume document is missing.")

    result = await prepare_application_form(
        user,
        app.job,
        resume_doc=resume_doc,
        cover_doc=cover_doc,
        browser_dir=BROWSER_DIR,
        screenshots_dir=SCREENSHOTS_DIR,
        headless=False,
        application_id=app.id,
        prior_apply_meta=decode_apply_meta(app.notes),
    )

    app.notes = encode_apply_meta(result)
    return app, result.warnings


async def confirm_submit(session: Session, user: User, application_id: int) -> tuple[Application, list[str]]:
    app = get_application(session, user, application_id)
    if app is None:
        raise LookupError("Application not found.")
    if app.status != ApplicationStatus.approved:
        raise ValueError(f"Cannot confirm submit for status {app.status.value}.")

    meta = decode_apply_meta(app.notes)
    warnings: list[str] = []
    if meta is None:
        warnings.append("No prepare step recorded; marking submitted based on your confirmation.")
    else:
        clicked, warn = await confirm_application_submit(
            page_url=str(meta.get("page_url") or app.job.url),
            submit_selector=meta.get("submit_selector"),
            browser_dir=BROWSER_DIR,
            headless=False,
            browser_use_session_id=meta.get("browser_use_session_id")
            if meta.get("browser_engine") == "browser_use" or meta.get("apply_flow") == "browser_use"
            else None,
        )
        if warn:
            warnings.append(warn)
        if not clicked:
            warnings.append("Submit was not clicked automatically — confirm you submitted manually.")

    app.status = ApplicationStatus.submitted
    app.submitted_at = datetime.now(UTC)
    return app, warnings


def mark_failed(session: Session, user: User, application_id: int, reason: str | None = None) -> Application:
    app = get_application(session, user, application_id)
    if app is None:
        raise LookupError("Application not found.")
    if app.status not in (ApplicationStatus.approved, ApplicationStatus.submitted):
        raise ValueError(f"Cannot mark failed from status {app.status.value}.")
    app.status = ApplicationStatus.failed
    if reason:
        prefix = app.notes or ""
        app.notes = f"{prefix}\nfailed: {reason}".strip()
    return app
