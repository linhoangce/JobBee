"""Document upload + management.

Endpoints:

  POST   /api/documents             multipart upload (file + kind + optional label)
  POST   /api/documents/text        JSON paste-in (kind + text + optional label)
  GET    /api/documents             list current user's documents
  GET    /api/documents/{id}/text   return the extracted plain text
  DELETE /api/documents/{id}        remove document (and underlying file)

Each user has at most one *active* resume and one *active* cover letter for
the agent to use — uploading a new one of a given kind replaces the prior one
so the UI doesn't have to manage selection state.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session, get_uploads_dir
from ..models import Document, DocumentKind
from ..schemas import DocumentKindLiteral, DocumentResponse, DocumentTextResponse
from ..services.text_extract import UnsupportedDocumentError, extract
from .profile import get_or_create_local_user

router = APIRouter(prefix="/documents", tags=["documents"])

# 10 MB cap. Resumes / cover letters should be well under this; we just want
# to keep someone from accidentally uploading a 200 MB scanned PDF.
MAX_BYTES = 10 * 1024 * 1024


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
        has_file=bool(doc.file_path),
        text_length=len(doc.content or ""),
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


def _delete_active_of_kind(session: Session, user_id: int, kind: DocumentKind) -> None:
    """Replace-on-upload: drop any existing doc of this kind for this user."""

    existing = session.execute(
        select(Document).where(Document.user_id == user_id, Document.kind == kind)
    ).scalars().all()
    for doc in existing:
        _delete_file_if_present(doc.file_path)
        session.delete(doc)


def _delete_file_if_present(path_str: str | None) -> None:
    if not path_str:
        return
    p = Path(path_str)
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        # Don't fail the request because the file vanished or is locked;
        # we'll just orphan the bytes.
        pass


def _safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in (".", "-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip("._") or "upload"
    return cleaned[:160]


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

    user_uploads = get_uploads_dir() / str(user.id)
    user_uploads.mkdir(parents=True, exist_ok=True)
    unique = secrets.token_hex(6)
    saved_path = user_uploads / f"{kind}_{unique}_{_safe_filename(filename)}"
    saved_path.write_bytes(raw)

    _delete_active_of_kind(session, user.id, DocumentKind(kind))

    doc = Document(
        user_id=user.id,
        kind=DocumentKind(kind),
        label=label or filename,
        content=result.text,
        file_path=str(saved_path),
        original_filename=filename,
        mime_type=file.content_type or result.mime_type,
        byte_size=len(raw),
    )
    session.add(doc)
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

    _delete_active_of_kind(session, user.id, DocumentKind(payload.kind))

    doc = Document(
        user_id=user.id,
        kind=DocumentKind(payload.kind),
        label=payload.label or ("Resume" if payload.kind == "resume" else "Cover letter"),
        content=text,
        file_path=None,
        original_filename=None,
        mime_type="text/plain",
        byte_size=len(text.encode("utf-8")),
    )
    session.add(doc)
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


@router.delete("/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(doc_id: int, session: Session = Depends(get_session)) -> None:
    user = get_or_create_local_user(session)
    doc = session.get(Document, doc_id)
    if doc is None or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found.")
    _delete_file_if_present(doc.file_path)
    session.delete(doc)
    session.commit()
