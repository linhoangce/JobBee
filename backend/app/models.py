"""ORM models for the foundation milestone.

Even though the app is single-user today, we model `User` explicitly so the
schema is forward-compatible with multi-user / multi-profile use later.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
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
    filtered_out = "filtered_out"  # dropped by hard filters or screening
    awaiting_approval = "awaiting_approval"  # card sent, waiting for Apply/Skip
    approved = "approved"  # queued for / being filled by the apply agent
    awaiting_submit = "awaiting_submit"  # filled; waiting for the user's Submit tap
    submitted = "submitted"
    rejected_by_user = "rejected_by_user"  # skipped
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

    # What the agent should look for and how often to ping the user (jobs_per_day,
    # min_match_score, excluded_companies, discovery_time, ...). See app/pipeline/prefs.py.
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

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
    # The Gmail refresh token and IMAP app password live in the secret store
    # (services/secrets.py), keyed by user id — never in this table.
    gmail_history_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    imap_host: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_user: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    # Base docs are the user's own resume / cover letter (one active per kind).
    # Tailored docs are per-application copies and are owned by that application.
    is_base: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
        index=True,
    )
    # Rendered PDF for upload to an ATS (tailored docs only).
    pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)

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
    # "llm" scores are never overwritten by keyword re-ranking; "heuristic" ones are.
    match_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(2048), nullable=True, index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    work_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    salary_text: Mapped[str | None] = mapped_column(String(255), nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_via: Mapped[str | None] = mapped_column(String(32), nullable=True)


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
    # Screening verdict: {"decision", "score", "summary", "reasons", "gaps"}.
    screen: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Why it was filtered out or skipped, for the dashboard and future filtering.
    stage_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # CV changes summary + the approval card's message id (to remove its buttons later).
    tailoring: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    card_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    user: Mapped[User] = relationship(back_populates="applications")
    job: Mapped[Job] = relationship()


class ApplyRun(TimestampMixin, Base):
    """One attempt by the Claude apply agent to fill (and, once approved, submit) an application.

    Status: queued → starting → running (⇄ needs_human, attended runs only) →
      awaiting_submit (form filled, tab parked, waiting for the user's Submit tap)
        ⇄ revising (changing answers the user corrected)
        → submitting → submitted | submit_unconfirmed
        → discarded (user said don't submit) | expired (no answer in time)
      or needs_you (set aside: only a human at the PC can continue)
      or blocked | failed | cancelled while filling.
    """

    __tablename__ = "apply_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("applications.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    ats: Mapped[str] = mapped_column(String(32), nullable=False, default="generic")
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    effort: Mapped[str] = mapped_column(String(16), nullable=False)
    # What the run is waiting on: {"type": "human_action", ...} or {"type": "revise", ...}.
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The user started it from their PC, so the agent may hand them the browser.
    attended: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    finish: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    iterations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    guard_trips: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    uninspected_clicks: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    review: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    submit_candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Filled form, parked for the user's Submit tap.
    tab_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    review_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    answers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    screenshot_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submit_card_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ApplyEvent(Base):
    """Step log of an apply run (tool calls, questions, guard blocks…) for the UI."""

    __tablename__ = "apply_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("apply_runs.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class MessagingLink(TimestampMixin, Base):
    """A user's chat on a messaging channel (Telegram today).

    Pairing: the app issues a one-time code (stored hashed); `/start <code>` in the
    bot binds `chat_id`/`from_id`. Only messages from the linked chat are acted on.
    """

    __tablename__ = "messaging_links"
    __table_args__ = (UniqueConstraint("user_id", "channel", name="uq_messaging_user_channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    link_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CallbackToken(Base):
    """One button press the bot will honour: an opaque nonce instead of raw ids.

    Tokens sharing a `group` (e.g. the options of one question) are spent together.
    """

    __tablename__ = "callback_tokens"

    nonce: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    group: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ChatMessage(Base):
    """Messages exchanged with the user, for the activity feed and keyboard clean-up."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)  # "in" | "out"
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="text")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    external_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application_id: Mapped[int | None] = mapped_column(
        ForeignKey("applications.id", ondelete="SET NULL"), nullable=True, index=True
    )
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("apply_runs.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class WatchlistCompany(TimestampMixin, Base):
    """A company whose public job board the agent polls (Greenhouse, Lever or Ashby)."""

    __tablename__ = "watchlist_companies"
    __table_args__ = (UniqueConstraint("user_id", "ats", "slug", name="uq_watchlist_board"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ats: Mapped[str] = mapped_column(String(32), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    careers_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class SiteAccount(TimestampMixin, Base):
    """An account the apply agent made on a job site (one per host, e.g. a Workday tenant).

    The password lives in the OS keyring (`services.secrets`, name `site:<host>`);
    this row is the index, since the keyring can't list its entries.
    """

    __tablename__ = "site_accounts"
    __table_args__ = (UniqueConstraint("user_id", "host", name="uq_site_account_host"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    login: Mapped[str] = mapped_column(String(320), nullable=False)
    # created → verified (email confirmed); "reset" after a password reset; "rejected" when
    # the site refused the saved password (it's reset before it's tried again).
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="created")
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DiscoveryRun(Base):
    """One job search: what the sources found and what the agent kept and sent."""

    __tablename__ = "discovery_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # manual | schedule | more
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    found: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    screened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kept: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sources: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmUsage(Base):
    """Every Claude call's tokens and estimated cost; budgets are enforced from this ledger."""

    __tablename__ = "llm_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    task: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_searches: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    application_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
