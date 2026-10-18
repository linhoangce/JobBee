"""Single-user profile endpoints.

`GET /api/profile` returns (and lazily creates) the local user's profile.
`PUT /api/profile` does a partial update; passing `mark_onboarded=true` stamps
`onboarded_at` so the UI knows to stop showing the wizard.
`POST /api/profile/import` runs the resume + GitHub importers and returns a
draft for the review screen (without persisting it).

We model multi-user in the schema (per `docs/architecture.md`) but JobBee only
runs locally and we never sign anyone in, so "the local user" is always
`users.id == 1`. If the row doesn't exist yet, we create it on the first GET.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import get_session, get_uploads_dir
from ..importers import ImporterError, import_github, import_resume, merge_drafts
from ..llm.factory import get_llm_provider
from ..models import Document, DocumentKind, User
from ..schemas import (
    ApplicationDefaults,
    ProfileDraft,
    ProfileImportResponse,
    ProfileResponse,
    ProfileUpdate,
)
from ..services.application_defaults import merge_application_defaults
from ..services.text_extract import UnsupportedDocumentError, extract

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

# Same cap as the documents router; we save the resume the user dropped so
# they don't have to upload it again before applying.
_MAX_BYTES = 10 * 1024 * 1024


# Fields whose presence (non-empty / non-default) signals a usable profile.
_COMPLETION_FIELDS = ("name", "headline", "target_roles", "work_modes")


def _application_defaults_response(user: User) -> ApplicationDefaults:
    merged = merge_application_defaults(user.application_defaults, location=user.location)
    stored = dict(user.application_defaults or {})
    saved = stored.get("saved_answers")
    saved_answers = saved if isinstance(saved, dict) else {}
    return ApplicationDefaults(
        phone=merged.get("phone") or stored.get("phone"),
        phone_extension=merged.get("phone_extension") or stored.get("phone_extension"),
        phone_device_type=merged.get("phone_device_type") or stored.get("phone_device_type"),
        country_phone_code=merged.get("country_phone_code") or stored.get("country_phone_code"),
        address_line1=merged.get("address_line1") or stored.get("address_line1"),
        address_line2=merged.get("address_line2") or stored.get("address_line2"),
        city=merged.get("city") or stored.get("city"),
        province=merged.get("province") or stored.get("province"),
        postal_code=merged.get("postal_code") or stored.get("postal_code"),
        country=merged.get("country") or stored.get("country"),
        work_country=merged.get("work_country") or stored.get("work_country"),
        work_authorization=merged.get("work_authorization"),  # type: ignore[arg-type]
        requires_sponsorship=merged.get("requires_sponsorship"),  # type: ignore[arg-type]
        referral_source=merged.get("referral_source") or stored.get("referral_source"),
        willing_to_relocate=merged.get("willing_to_relocate") or stored.get("willing_to_relocate"),  # type: ignore[arg-type]
        saved_answers={str(k): str(v) for k, v in saved_answers.items()},
    )


def _to_response(user: User) -> ProfileResponse:
    return ProfileResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        headline=user.headline,
        location=user.location,
        years_experience=user.years_experience,
        min_salary=user.min_salary,
        summary=user.summary,
        target_roles=list(user.target_roles or []),
        target_locations=list(user.target_locations or []),
        work_modes=list(user.work_modes or []),
        skills=list(user.skills or []),
        links=dict(user.links or {}),
        application_defaults=_application_defaults_response(user),
        approval_top_n=user.approval_top_n,
        onboarded_at=user.onboarded_at,
        is_complete=_is_complete(user),
    )


def _is_complete(user: User) -> bool:
    if user.onboarded_at is not None:
        return True
    # Soft completion: enough fields filled that the agent could realistically search.
    for field in _COMPLETION_FIELDS:
        value = getattr(user, field, None)
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        if isinstance(value, (list, dict)) and len(value) == 0:
            return False
    return True


def get_or_create_local_user(session: Session) -> User:
    """Return the single local-user row, creating it on first call."""

    user = session.execute(select(User).order_by(User.id).limit(1)).scalar_one_or_none()
    if user is None:
        user = User(name="")
        session.add(user)
        session.commit()
        session.refresh(user)
    return user


@router.get("", response_model=ProfileResponse)
def read_profile(session: Session = Depends(get_session)) -> ProfileResponse:
    user = get_or_create_local_user(session)
    return _to_response(user)


@router.put("", response_model=ProfileResponse)
def update_profile(
    payload: ProfileUpdate,
    session: Session = Depends(get_session),
) -> ProfileResponse:
    user = get_or_create_local_user(session)
    data = payload.model_dump(exclude_unset=True)

    mark_onboarded = data.pop("mark_onboarded", None)
    app_defaults_patch = data.pop("application_defaults", None)

    for field, value in data.items():
        setattr(user, field, value)

    if app_defaults_patch is not None:
        existing = dict(user.application_defaults or {})
        saved_patch = app_defaults_patch.pop("saved_answers", None)
        for key, value in app_defaults_patch.items():
            if value is None:
                existing.pop(key, None)
            else:
                existing[key] = value
        if saved_patch is not None:
            saved = dict(existing.get("saved_answers") or {})
            saved.update({str(k): str(v) for k, v in saved_patch.items() if v})
            existing["saved_answers"] = saved
        user.application_defaults = existing

    if mark_onboarded:
        user.onboarded_at = datetime.now(timezone.utc)
    elif mark_onboarded is False:
        user.onboarded_at = None

    session.commit()
    session.refresh(user)
    return _to_response(user)


def _safe_filename(name: str) -> str:
    keep = []
    for ch in name:
        if ch.isalnum() or ch in (".", "-", "_"):
            keep.append(ch)
        else:
            keep.append("_")
    cleaned = "".join(keep).strip("._") or "upload"
    return cleaned[:160]


def _replace_active_resume(
    session: Session,
    user_id: int,
    *,
    raw: bytes,
    filename: str,
    text: str,
    mime: str | None,
) -> Document:
    """Drop any existing resume for the user, save the new bytes, persist a row."""

    existing = session.execute(
        select(Document).where(
            Document.user_id == user_id, Document.kind == DocumentKind.resume
        )
    ).scalars().all()
    for old in existing:
        if old.file_path:
            try:
                p = Path(old.file_path)
                if p.is_file():
                    p.unlink()
            except OSError:
                pass
        session.delete(old)

    user_uploads = get_uploads_dir() / str(user_id)
    user_uploads.mkdir(parents=True, exist_ok=True)
    unique = secrets.token_hex(6)
    saved_path = user_uploads / f"resume_{unique}_{_safe_filename(filename)}"
    saved_path.write_bytes(raw)

    doc = Document(
        user_id=user_id,
        kind=DocumentKind.resume,
        label=filename,
        content=text,
        file_path=str(saved_path),
        original_filename=filename,
        mime_type=mime,
        byte_size=len(raw),
    )
    session.add(doc)
    session.commit()
    session.refresh(doc)
    return doc


@router.post("/import", response_model=ProfileImportResponse)
async def import_profile(
    github_username: str | None = Form(default=None),
    github_token: str | None = Form(default=None),
    resume_file: UploadFile | None = File(default=None),
    session: Session = Depends(get_session),
) -> ProfileImportResponse:
    """Run the resume + GitHub importers and return a non-persisted draft.

    At least one of `resume_file` / `github_username` must be provided. We
    persist the resume `Document` as a side effect (so the user keeps the
    bytes for later applications), but we deliberately do NOT touch the
    `User` profile row -- that happens only when the user clicks Confirm
    in the review screen, via `PUT /api/profile`.
    """

    if resume_file is None and not (github_username or "").strip():
        raise HTTPException(
            status_code=400,
            detail="Provide at least one of: resume_file, github_username.",
        )

    user = get_or_create_local_user(session)
    drafts = []  # priority order: highest first
    warnings: list[str] = []
    document_id: int | None = None

    # --- 1. Resume (LLM) ---
    if resume_file is not None:
        raw = await resume_file.read()
        if not raw:
            raise HTTPException(status_code=400, detail="Empty resume file.")
        if len(raw) > _MAX_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Resume too large (max {_MAX_BYTES // (1024 * 1024)} MB).",
            )
        filename = resume_file.filename or "resume"
        try:
            extracted = extract(filename, raw)
        except UnsupportedDocumentError as e:
            raise HTTPException(status_code=415, detail=str(e)) from e
        if not extracted.text.strip():
            raise HTTPException(
                status_code=422,
                detail=(
                    "No text could be extracted from the resume. If it's a"
                    " scanned PDF, paste the text into the manual form instead."
                ),
            )

        # Persist the bytes immediately so the user can come back to them.
        doc = _replace_active_resume(
            session,
            user.id,
            raw=raw,
            filename=filename,
            text=extracted.text,
            mime=resume_file.content_type or extracted.mime_type,
        )
        document_id = doc.id

        provider = get_llm_provider()
        try:
            resume_draft = await import_resume(
                extracted.text,
                provider=provider,
                label=f"resume ({filename})",
            )
            drafts.append(resume_draft)
            if not resume_draft.data:
                warnings.append(
                    "Resume was read but the model did not extract any profile fields. "
                    "Try again or fill the form manually."
                )
        except ImporterError as e:
            warnings.append(f"Resume extraction failed: {e}")
            logger.warning("Resume import failed: %s", e)

    # --- 2. GitHub (no LLM) ---
    if github_username and github_username.strip():
        try:
            gh_draft = await import_github(
                github_username.strip(), token=(github_token or "").strip() or None
            )
            drafts.append(gh_draft)
        except ImporterError as e:
            warnings.append(f"GitHub import failed: {e}")
            logger.warning("GitHub import failed: %s", e)

    if not drafts and not warnings:
        # Both inputs were missing or empty after validation (shouldn't get
        # here because of the guard above, but belt-and-suspenders).
        raise HTTPException(status_code=400, detail="No importers ran.")

    merged = merge_drafts(drafts)

    try:
        draft = ProfileDraft.model_validate(merged.data)
    except Exception as e:  # pragma: no cover - safety net
        logger.exception("ProfileDraft validation failed")
        raise HTTPException(
            status_code=500,
            detail=f"Merged draft failed schema validation: {e}",
        ) from e

    return ProfileImportResponse(
        draft=draft,
        sources=merged.sources,
        document_id=document_id,
        warnings=warnings,
    )
