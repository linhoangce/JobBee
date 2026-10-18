"""Approval queue: promote top-N matches for user review."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Application, ApplicationStatus, Job, User

DEFAULT_APPROVAL_TOP_N = 5

def resolve_top_n(user: User, top_n: int | None = None) -> int:
    if top_n is not None:
        return top_n
    value = getattr(user, "approval_top_n", None)
    return value if value is not None else DEFAULT_APPROVAL_TOP_N


def _ranked_matched_applications(session: Session, user: User) -> list[Application]:
    stmt = (
        select(Application)
        .join(Job, Application.job_id == Job.id)
        .where(
            Application.user_id == user.id,
            Application.status == ApplicationStatus.matched,
        )
        .order_by(Job.match_score.desc().nulls_last(), Job.created_at.desc())
    )
    return list(session.execute(stmt).scalars().all())


def queue_top_matches(session: Session, user: User, top_n: int) -> int:
    """Move the top-N scored `matched` applications into `awaiting_approval`.

    Any applications currently awaiting approval that are not in the new top-N
    are demoted back to `matched`.
    """

    top_n = max(1, min(top_n, 50))

    awaiting = session.execute(
        select(Application).where(
            Application.user_id == user.id,
            Application.status == ApplicationStatus.awaiting_approval,
        )
    ).scalars().all()
    for app in awaiting:
        app.status = ApplicationStatus.matched

    candidates = _ranked_matched_applications(session, user)
    promoted = candidates[:top_n]
    for app in promoted:
        app.status = ApplicationStatus.awaiting_approval

    return len(promoted)


def list_applications(
    session: Session,
    user: User,
    *,
    status: ApplicationStatus | None = None,
) -> list[Application]:
    stmt = (
        select(Application)
        .join(Job, Application.job_id == Job.id)
        .where(Application.user_id == user.id)
    )
    if status is not None:
        stmt = stmt.where(Application.status == status)
    stmt = stmt.order_by(Job.match_score.desc().nulls_last(), Application.updated_at.desc())
    return list(session.execute(stmt).scalars().all())


def get_application(session: Session, user: User, application_id: int) -> Application | None:
    return session.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user.id,
        )
    ).scalar_one_or_none()


def approve_application(session: Session, user: User, application_id: int) -> Application:
    app = get_application(session, user, application_id)
    if app is None:
        raise LookupError("Application not found.")
    if app.status != ApplicationStatus.awaiting_approval:
        raise ValueError(f"Cannot approve application in status {app.status.value}.")
    app.status = ApplicationStatus.approved
    return app


def reject_application(session: Session, user: User, application_id: int) -> Application:
    app = get_application(session, user, application_id)
    if app is None:
        raise LookupError("Application not found.")
    if app.status != ApplicationStatus.awaiting_approval:
        raise ValueError(f"Cannot reject application in status {app.status.value}.")
    app.status = ApplicationStatus.rejected_by_user
    return app
