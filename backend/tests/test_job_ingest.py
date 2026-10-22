"""Job ingest helper tests."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import Document, DocumentKind, User
from app.services.documents import get_base_document


def test_get_base_resume_prefers_non_tailored_copy() -> None:
    with TestClient(app) as client:
        client.put(
            "/api/profile",
            json={"name": "Lin", "target_roles": ["Backend"], "skills": ["python"]},
        )
        client.post(
            "/api/documents/text",
            json={"kind": "resume", "text": "Base body", "label": "Uploaded resume"},
        )

    with SessionLocal() as session:
        user = session.execute(select(User)).scalar_one()
        session.add(
            Document(
                user_id=user.id,
                kind=DocumentKind.resume,
                label="Tailored resume — Acme",
                content="Tailored body",
                is_base=False,
            )
        )
        session.commit()

        resume = get_base_document(session, user.id, DocumentKind.resume)
        assert resume is not None
        assert resume.label == "Uploaded resume"
