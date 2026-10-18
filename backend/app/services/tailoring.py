"""Persist tailored documents for approved applications."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..agents.tailor_agent import tailor_for_job
from ..llm.factory import get_llm_provider
from ..models import Application, ApplicationStatus, Document, DocumentKind, Job, User


def _is_tailored_doc(doc: Document) -> bool:
    label = (doc.label or "").lower()
    return label.startswith("tailored resume") or label.startswith("cover letter —")


def _get_base_resume(session: Session, user: User) -> Document | None:
    docs = session.execute(
        select(Document)
        .where(Document.user_id == user.id, Document.kind == DocumentKind.resume)
        .order_by(Document.id.desc())
    ).scalars().all()
    for doc in docs:
        if not _is_tailored_doc(doc):
            return doc
    return docs[0] if docs else None


def _get_base_cover(session: Session, user: User) -> Document | None:
    docs = session.execute(
        select(Document)
        .where(Document.user_id == user.id, Document.kind == DocumentKind.cover_letter)
        .order_by(Document.id.desc())
    ).scalars().all()
    for doc in docs:
        if not _is_tailored_doc(doc):
            return doc
    return None


def _delete_linked_doc(session: Session, doc_id: int | None) -> None:
    if doc_id is None:
        return
    doc = session.get(Document, doc_id)
    if doc is not None:
        session.delete(doc)


async def tailor_application(session: Session, user: User, application_id: int) -> tuple[Application, list[str]]:
    app = session.execute(
        select(Application).where(
            Application.id == application_id,
            Application.user_id == user.id,
        )
    ).scalar_one_or_none()
    if app is None:
        raise LookupError("Application not found.")
    if app.status != ApplicationStatus.approved:
        raise ValueError(f"Cannot tailor application in status {app.status.value}.")

    job = session.get(Job, app.job_id)
    if job is None:
        raise LookupError("Job not found for application.")

    base_resume = _get_base_resume(session, user)
    if base_resume is None or not (base_resume.content or "").strip():
        raise ValueError("Upload or paste a base resume before tailoring.")

    base_cover = _get_base_cover(session, user)
    result = await tailor_for_job(
        user,
        job,
        resume=base_resume,
        cover_letter=base_cover,
        provider=get_llm_provider(),
    )

    _delete_linked_doc(session, app.resume_doc_id)
    _delete_linked_doc(session, app.cover_doc_id)

    resume_doc = Document(
        user_id=user.id,
        kind=DocumentKind.resume,
        label=f"Tailored resume — {job.title[:80]}",
        content=result.resume_text,
        mime_type="text/plain",
        byte_size=len(result.resume_text.encode("utf-8")),
    )
    cover_doc = Document(
        user_id=user.id,
        kind=DocumentKind.cover_letter,
        label=f"Cover letter — {job.title[:80]}",
        content=result.cover_text,
        mime_type="text/plain",
        byte_size=len(result.cover_text.encode("utf-8")),
    )
    session.add(resume_doc)
    session.add(cover_doc)
    session.flush()

    app.resume_doc_id = resume_doc.id
    app.cover_doc_id = cover_doc.id
    return app, result.warnings
