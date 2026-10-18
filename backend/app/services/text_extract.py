"""Extract plain text from user-uploaded resumes / cover letters.

Supports `.pdf` (pypdf), `.docx` (python-docx), and plain text (`.txt`, `.md`,
anything else we can decode as UTF-8). Unsupported binary types raise
`UnsupportedDocumentError` so the caller can return a 415.

This module is pure; it doesn't touch the DB or FastAPI. The router decides
where the file is saved and how to surface errors.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from pypdf import PdfReader
from pypdf.errors import PdfReadError


class UnsupportedDocumentError(ValueError):
    """Raised when a file can't be turned into plain text."""


# (extension lowercased -> canonical mime we report back to the UI)
_PDF_EXT = {".pdf"}
_DOCX_EXT = {".docx"}
_TEXT_EXT = {".txt", ".md", ".markdown", ".rtf"}

MIME_BY_EXT: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rtf": "application/rtf",
}


@dataclass
class ExtractResult:
    text: str
    mime_type: str


def _ext_of(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def _extract_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
    except PdfReadError as e:  # malformed file
        raise UnsupportedDocumentError(f"PDF could not be parsed: {e}") from e
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            # Some pages (scanned images, encrypted) can fail individually.
            # We swallow per-page failures so a mostly-good PDF still works.
            continue
    return "\n\n".join(p.strip() for p in parts if p.strip())


def _extract_docx(data: bytes) -> str:
    # python-docx is heavy to import; do it lazily so the rest of the API
    # is not delayed at startup.
    from docx import Document as DocxDocument  # type: ignore[import-not-found]

    try:
        doc = DocxDocument(io.BytesIO(data))
    except Exception as e:  # python-docx raises generic exceptions on bad files
        raise UnsupportedDocumentError(f"DOCX could not be parsed: {e}") from e

    paragraphs = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text and cell.text.strip():
                    paragraphs.append(cell.text.strip())
    return "\n".join(paragraphs)


def _extract_text(data: bytes) -> str:
    # Try common encodings before giving up; resumes from Word sometimes export
    # as Windows-1252. Strip UTF-8 BOM so LLM prompts stay clean.
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnsupportedDocumentError("Could not decode file as text.")


def extract(filename: str, data: bytes) -> ExtractResult:
    """Pick the right extractor based on the filename extension.

    Raises `UnsupportedDocumentError` if the extension isn't one we handle or
    the file is malformed beyond repair.
    """

    ext = _ext_of(filename)
    if ext in _PDF_EXT:
        return ExtractResult(text=_extract_pdf(data), mime_type=MIME_BY_EXT[ext])
    if ext in _DOCX_EXT:
        return ExtractResult(text=_extract_docx(data), mime_type=MIME_BY_EXT[ext])
    if ext in _TEXT_EXT or ext == "":
        # Empty extension: assume plain text (e.g. resume.txt renamed).
        return ExtractResult(
            text=_extract_text(data),
            mime_type=MIME_BY_EXT.get(ext, "text/plain"),
        )

    raise UnsupportedDocumentError(
        f"Unsupported file type {ext!r}. Upload .pdf, .docx, .txt, or .md."
    )
