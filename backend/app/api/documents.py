"""Document upload + management.

Endpoints:

  POST   /api/documents             multipart upload (file + kind + optional label)
  POST   /api/documents/text        JSON paste-in (kind + text + optional label)
  GET    /api/documents             list current user's documents
  GET    /api/documents/{id}/text   return the extracted plain text
  GET    /api/documents/{id}/file   the PDF (tailored) or the uploaded original
  GET    /api/documents/styles      the CV / cover letter styles
  GET    /api/documents/styles/{style}/preview?kind=resume|cover_letter
                                    a style applied to your latest tailored CV (or a sample)
  DELETE /api/documents/{id}        remove document (and underlying file)

Each user has at most one *base* resume and one *base* cover letter for the
agent to use — uploading a new one of a given kind replaces the prior base
document so the UI doesn't have to manage selection state. Tailored copies
owned by applications are listed too but never replaced by an upload.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import BACKEND_DIR
from ..db import get_session, get_uploads_dir
from ..documents.render_pdf import (
    SAMPLE_CV,
    SAMPLE_LETTER,
    STYLES,
    cover_letter_html,
    resume_html,
)
from ..models import Application, Document, DocumentKind, Job
from ..pipeline.tailor import StructuredCv
from ..schemas import (
    DocumentKindLiteral,
    DocumentResponse,
    DocumentStyleInfo,
    DocumentTextResponse,
)
from ..services.documents import delete_file_if_present, replace_base_document
from ..services.text_extract import UnsupportedDocumentError, extract
from .profile import get_or_create_local_user

router = APIRouter(prefix="/documents", tags=["documents"])

# 10 MB cap. Resumes / cover letters should be well under this; we just want
# to keep someone from accidentally uploading a 200 MB scanned PDF.
MAX_BYTES = 10 * 1024 * 1024

# Files are only ever served from JobBee's own data folder.
SERVE_ROOTS: list[Path] = [BACKEND_DIR / "data"]


def confined_file(path_str: str | None) -> Path:
    """The file at `path_str` if it exists inside a serve root, else 404."""

    if not path_str:
        raise HTTPException(status_code=404, detail="No file for this document.")
    path = Path(path_str).resolve()
    roots = [*SERVE_ROOTS, get_uploads_dir()]
    if not any(path.is_relative_to(root.resolve()) for root in roots) or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found.")
    return path


class PasteDocumentRequest(BaseModel):
    kind: DocumentKindLiteral
    text: str
    label: str | None = None


def _to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        kind=doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind),
        label=doc.label or "",
        original_filename=doc.original_filename,
        mime_type=doc.mime_type,
        byte_size=doc.byte_size,
        has_file=bool(doc.pdf_path or doc.file_path),
        text_length=len(doc.content or ""),
        is_base=doc.is_base,
        application_id=doc.application_id,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    kind: DocumentKindLiteral = Form(...),
    label: str | None = Form(default=None),
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> DocumentResponse:
    user = get_or_create_local_user(session)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status_code=413, detail=f"File too large (max {MAX_BYTES // (1024 * 1024)} MB)."
        )

    filename = file.filename or "upload"
    try:
        result = extract(filename, raw)
    except UnsupportedDocumentError as e:
        raise HTTPException(status_code=415, detail=str(e)) from e

    if not result.text.strip():
        raise HTTPException(
            status_code=422,
            detail="No text could be extracted. If this is a scanned PDF, paste the text instead.",
        )

    doc = replace_base_document(
        session,
        user.id,
        DocumentKind(kind),
        text=result.text,
        label=label or filename,
        raw=raw,
        filename=filename,
        mime_type=file.content_type or result.mime_type,
    )
    session.commit()
    session.refresh(doc)
    return _to_response(doc)


@router.post("/text", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
def paste_document(
    payload: PasteDocumentRequest,
    session: Session = Depends(get_session),
) -> DocumentResponse:
    user = get_or_create_local_user(session)
    text = payload.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is empty.")

    doc = replace_base_document(
        session,
        user.id,
        DocumentKind(payload.kind),
        text=text,
        label=payload.label or ("Resume" if payload.kind == "resume" else "Cover letter"),
        mime_type="text/plain",
    )
    session.commit()
    session.refresh(doc)
    return _to_response(doc)


@router.get("", response_model=list[DocumentResponse])
def list_documents(session: Session = Depends(get_session)) -> list[DocumentResponse]:
    user = get_or_create_local_user(session)
    docs = session.execute(
        select(Document).where(Document.user_id == user.id).order_by(Document.id.desc())
    ).scalars().all()
    return [_to_response(d) for d in docs]


@router.get("/styles", response_model=list[DocumentStyleInfo])
def list_styles() -> list[DocumentStyleInfo]:
    return [
        DocumentStyleInfo(id=s.id, name=s.name, description=s.description)
        for s in STYLES.values()
    ]


# The preview is static markup: no scripts, nothing loaded from elsewhere.
_PREVIEW_CSP = "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'self'"


@router.get("/styles/{style_id}/preview", response_class=HTMLResponse)
def preview_style(
    style_id: str,
    kind: Literal["resume", "cover_letter"] = "resume",
    session: Session = Depends(get_session),
) -> HTMLResponse:
    if style_id not in STYLES:
        raise HTTPException(status_code=404, detail="Unknown CV style.")
    user = get_or_create_local_user(session)
    cv, letter, company, role = SAMPLE_CV, SAMPLE_LETTER, "Acme Corp", "Backend Engineer"
    recent = session.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id, Application.resume_doc_id.is_not(None))
        .order_by(Application.updated_at.desc())
        .limit(30)
    ).all()
    for app, job in recent:
        tailoring = app.tailoring or {}
        if tailoring.get("cv"):
            cv = StructuredCv.model_validate(tailoring["cv"])
            letter = tailoring.get("cover_letter") or SAMPLE_LETTER
            company, role = job.company, job.title
            break
    markup = (
        resume_html(cv, style_id)
        if kind == "resume"
        else cover_letter_html(cv, letter, company, role, style_id)
    )
    return HTMLResponse(markup, headers={"Content-Security-Policy": _PREVIEW_CSP})


@router.get("/{doc_id}/text", response_model=DocumentTextResponse)
def get_document_text(
    doc_id: int, session: Session = Depends(get_session)
) -> DocumentTextResponse:
    user = get_or_create_local_user(session)
    doc = session.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    return DocumentTextResponse(
        id=doc.id,
        kind=doc.kind.value if hasattr(doc.kind, "value") else str(doc.kind),
        text=doc.content or "",
    )


@router.get("/{doc_id}/file")
def get_document_file(doc_id: int, session: Session = Depends(get_session)) -> FileResponse:
    user = get_or_create_local_user(session)
    doc = session.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    path = confined_file(doc.pdf_path or doc.file_path)
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else doc.mime_type
    return FileResponse(
        path,
        media_type=media_type or "application/octet-stream",
        filename=path.name,
        content_disposition_type="inline",
    )


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, session: Session = Depends(get_session)) -> None:
    user = get_or_create_local_user(session)
    doc = session.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    delete_file_if_present(doc.file_path)
    delete_file_if_present(doc.pdf_path)
    session.delete(doc)
    session.commit()
