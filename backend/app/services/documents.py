"""Base vs. tailored document bookkeeping.

A user has at most one active *base* document per kind (their own resume /
cover letter). Tailored documents are per-application copies owned by that
application; replacing a base document must never touch them.
"""

from __future__ import annotations

import re
import secrets
import zipfile
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_uploads_dir
from ..models import Document, DocumentKind


def safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in (".", "-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip("._") or "upload"
    return cleaned[:160]


def delete_file_if_present(path_str: str | None) -> None:
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


def get_base_document(session: Session, user_id: int, kind: DocumentKind) -> Document | None:
    """Newest base document of this kind, ignoring per-application tailored copies."""

    return session.execute(
        select(Document)
        .where(
            Document.user_id == user_id,
            Document.kind == kind,
            Document.is_base.is_(True),
        )
        .order_by(Document.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def page_budget(doc: Document | None) -> int:
    """How many pages the user's own resume runs to; tailored CVs get the same budget.

    Read from the uploaded PDF (or the page count Word saved in a .docx), else
    estimated from the word count.
    """

    path = Path(doc.file_path) if doc is not None and doc.file_path else None
    try:
        if path is not None and path.suffix.lower() == ".pdf":
            return max(1, min(3, len(PdfReader(path).pages)))
        if path is not None and path.suffix.lower() == ".docx":
            with zipfile.ZipFile(path) as archive:
                found = re.search(rb"<Pages>(\d+)</Pages>", archive.read("docProps/app.xml"))
            if found:
                return max(1, min(3, int(found.group(1))))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile, PdfReadError):
        pass
    words = len(((doc.content if doc is not None else None) or "").split())
    return 1 if words <= 750 else 2


def replace_base_document(
    session: Session,
    user_id: int,
    kind: DocumentKind,
    *,
    text: str,
    label: str,
    raw: bytes | None = None,
    filename: str | None = None,
    mime_type: str | None = None,
) -> Document:
    """Swap the user's base document of `kind`, keeping tailored copies intact.

    Saves `raw` under the user's uploads dir when given. The caller commits.
    """

    existing = (
        session.execute(
            select(Document).where(
                Document.user_id == user_id,
                Document.kind == kind,
                Document.is_base.is_(True),
            )
        )
        .scalars()
        .all()
    )
    for old in existing:
        delete_file_if_present(old.file_path)
        session.delete(old)

    saved_path: Path | None = None
    if raw is not None:
        user_uploads = get_uploads_dir() / str(user_id)
        user_uploads.mkdir(parents=True, exist_ok=True)
        unique = secrets.token_hex(6)
        saved_path = user_uploads / f"{kind.value}_{unique}_{safe_filename(filename or 'upload')}"
        saved_path.write_bytes(raw)

    doc = Document(
        user_id=user_id,
        kind=kind,
        label=label,
        content=text,
        file_path=str(saved_path) if saved_path else None,
        original_filename=filename if raw is not None else None,
        mime_type=mime_type,
        byte_size=len(raw) if raw is not None else len(text.encode("utf-8")),
        is_base=True,
    )
    session.add(doc)
    session.flush()
    return doc
