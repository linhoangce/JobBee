"""ORM models for the foundation milestone.

Even though the app is single-user today, we model `User` explicitly so the
schema is forward-compatible with multi-user / multi-profile use later.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class DocumentKind(str, enum.Enum):
    resume = "resume"
    cover_letter = "cover_letter"


class ApplicationStatus(str, enum.Enum):
    matched = "matched"
    awaiting_approval = "awaiting_approval"
    approved = "approved"
    submitted = "submitted"
    rejected_by_user = "rejected_by_user"
    failed = "failed"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class WorkMode(str, enum.Enum):
    remote = "remote"
    hybrid = "hybrid"
    onsite = "onsite"


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)

    # --- Profile fields (filled during onboarding) ---
    headline: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    years_experience: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_salary: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    # JSON columns store small homogeneous lists. SQLite stores them as TEXT.
    target_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    target_locations: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    work_modes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    skills: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    links: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # Common answers for ATS screening questions (work auth, sponsorship, referral, etc.).
    application_defaults: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)

    # How many top-scored matches to send to the approval queue after search.
    approval_top_n: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    documents: Mapped[list[Document]] = relationship(back_populates="user", cascade="all, delete-orphan")
    applications: Mapped[list[Application]] = relationship(back_populates="user", cascade="all, delete-orphan")
    email_sync_state: Mapped[EmailSyncState | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )


class AppConfig(Base):
    """Key-value store for one-time local setup (e.g. Gmail OAuth app credentials)."""

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")


class EmailSyncState(TimestampMixin, Base):
    """Inbox connection + incremental sync cursor (one row per user)."""

    __tablename__ = "email_sync_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    connected_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gmail_refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    gmail_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_app_password: Mapped[str | None] = mapped_column(Text, nullable=True)
    imap_folder: Mapped[str | None] = mapped_column(String(255), nullable=True, default="INBOX")
    imap_uid_validity: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_last_uid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    oauth_state: Mapped[str | None] = mapped_column(String(128), nullable=True)

    user: Mapped[User] = relationship(back_populates="email_sync_state")


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[DocumentKind] = mapped_column(Enum(DocumentKind), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    # Either inline content or a file pointer; keep both so we can support uploads
    # and direct paste-in. `content` always holds extracted plain text for the LLM,
    # `file_path` points at the original binary if the user uploaded one.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    byte_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    user: Mapped[User] = relationship(back_populates="documents")


class Job(TimestampMixin, Base):
    __tablename__ = "jobs"
    __table_args__ = (UniqueConstraint("source", "external_id", name="uq_jobs_source_external"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class Application(TimestampMixin, Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    resume_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    cover_doc_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)
    status: Mapped[ApplicationStatus] = mapped_column(
        Enum(ApplicationStatus), nullable=False, default=ApplicationStatus.matched
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship()
