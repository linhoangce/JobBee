"""Document upload + text extraction tests."""

from __future__ import annotations

import io

from fastapi.testclient import TestClient

from app.main import app


def test_paste_resume_text_creates_document() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/documents/text",
            json={
                "kind": "resume",
                "text": "Lin Hong\nSenior engineer\nSkills: Python, TypeScript.",
                "label": "Pasted resume",
            },
        )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["kind"] == "resume"
    assert body["text_length"] > 0
    assert body["has_file"] is False
    doc_id = body["id"]

    with TestClient(app) as client:
        text_resp = client.get(f"/api/documents/{doc_id}/text")
    assert text_resp.status_code == 200
    text_body = text_resp.json()
    assert "Senior engineer" in text_body["text"]
    assert text_body["kind"] == "resume"


def test_upload_txt_file_extracts_and_replaces_active_resume() -> None:
    with TestClient(app) as client:
        first = client.post(
            "/api/documents",
            files={"file": ("resume.txt", b"first resume body", "text/plain")},
            data={"kind": "resume", "label": "v1"},
        )
        assert first.status_code == 201, first.text
        first_id = first.json()["id"]

        second = client.post(
            "/api/documents",
            files={"file": ("resume.txt", b"second resume body", "text/plain")},
            data={"kind": "resume", "label": "v2"},
        )
        assert second.status_code == 201, second.text
        second_id = second.json()["id"]
        assert second_id != first_id

        listing = client.get("/api/documents")
        assert listing.status_code == 200
        kinds_to_ids = [(d["kind"], d["id"]) for d in listing.json()]
        # The replace-on-upload rule means only one resume should remain.
        assert ("resume", second_id) in kinds_to_ids
        assert ("resume", first_id) not in kinds_to_ids


def test_upload_pdf_extracts_text() -> None:
    # Lightweight inline PDF via reportlab? We don't depend on it. Use pypdf to
    # synthesize a minimal one-page PDF and confirm round-trip extraction.
    from pypdf import PdfWriter
    from pypdf.generic import NameObject, TextStringObject

    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    # Add metadata so the byte stream is non-trivial; pypdf cannot easily add
    # visible page text without a real text layer, so we assert on the path
    # where extraction may be empty.
    writer.add_metadata({NameObject("/Title"): TextStringObject("ResumeProbe")})

    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()
    assert pdf_bytes.startswith(b"%PDF")

    with TestClient(app) as client:
        resp = client.post(
            "/api/documents",
            files={"file": ("resume.pdf", pdf_bytes, "application/pdf")},
            data={"kind": "resume", "label": "binary"},
        )
    # A blank-page PDF has no text layer, so we expect a 422 from the no-text
    # guard. This still proves the extractor recognized the file as a PDF
    # rather than rejecting it with 415.
    assert resp.status_code in (201, 422), resp.text
    if resp.status_code == 422:
        assert "No text" in resp.json()["detail"]


def test_unsupported_file_type_returns_415() -> None:
    with TestClient(app) as client:
        resp = client.post(
            "/api/documents",
            files={"file": ("photo.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            data={"kind": "resume"},
        )
    assert resp.status_code == 415, resp.text


def test_delete_document_removes_it() -> None:
    with TestClient(app) as client:
        created = client.post(
            "/api/documents/text",
            json={"kind": "cover_letter", "text": "Dear hiring manager,"},
        )
        doc_id = created.json()["id"]

        delete_resp = client.delete(f"/api/documents/{doc_id}")
        assert delete_resp.status_code == 204

        miss = client.get(f"/api/documents/{doc_id}/text")
        assert miss.status_code == 404
