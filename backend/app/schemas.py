"""Pydantic request/response schemas exposed by the HTTP API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkModeLiteral = Literal["remote", "hybrid", "onsite"]
WorkAuthorizationLiteral = Literal["yes_no_restrictions", "yes_with_restrictions", "no"]
YesNoLiteral = Literal["yes", "no"]
DocumentKindLiteral = Literal["resume", "cover_letter"]
ApplicationStatusLiteral = Literal[
    "matched",
    "awaiting_approval",
    "approved",
    "submitted",
    "rejected_by_user",
    "failed",
]


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    app_env: str
    version: str
    db: bool
    llm_provider: str
    llm_model: str


class SettingsResponse(BaseModel):
    llm_provider: Literal["openai", "ollama"]
    llm_base_url: str
    llm_model: str
    llm_temperature: float
    llm_timeout_s: float
    apply_vision_enabled: bool
    apply_vision_model: str
    has_api_key: bool


class SettingsUpdate(BaseModel):
    llm_provider: Literal["openai", "ollama"] | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    llm_timeout_s: float | None = Field(default=None, gt=0.0, le=600.0)
    apply_vision_enabled: bool | None = None
    apply_vision_model: str | None = None


class LLMTestRequest(BaseModel):
    """Optional fields override saved settings without persisting them."""

    provider: Literal["openai", "ollama"] | None = None
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    prompt: str = "Reply with just the word: pong"


class LLMTestResponse(BaseModel):
    ok: bool
    provider: str
    model: str
    content: str | None = None
    error: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class ApplicationDefaults(BaseModel):
    """Canonical answers reused across ATS application forms."""

    phone: str | None = None
    phone_extension: str | None = None
    phone_device_type: str | None = None
    country_phone_code: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    country: str | None = None
    work_country: str | None = None
    work_authorization: WorkAuthorizationLiteral | None = None
    requires_sponsorship: YesNoLiteral | None = None
    referral_source: str | None = None
    willing_to_relocate: YesNoLiteral | None = None
    saved_answers: dict[str, str] = Field(default_factory=dict)


class ProfileResponse(BaseModel):
    """Single-user profile, surfaced for the onboarding wizard and the home page."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str | None = None
    headline: str | None = None
    location: str | None = None
    years_experience: float | None = None
    min_salary: int | None = None
    summary: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    work_modes: list[WorkModeLiteral] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    application_defaults: ApplicationDefaults = Field(default_factory=ApplicationDefaults)
    approval_top_n: int = Field(default=5, ge=1, le=50)
    onboarded_at: datetime | None = None
    is_complete: bool = False


class ProfileUpdate(BaseModel):
    """Partial-update payload for `PUT /api/profile`. All fields optional."""

    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    headline: str | None = Field(default=None, max_length=255)
    location: str | None = Field(default=None, max_length=255)
    years_experience: float | None = Field(default=None, ge=0, le=80)
    min_salary: int | None = Field(default=None, ge=0)
    summary: str | None = None
    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    work_modes: list[WorkModeLiteral] | None = None
    skills: list[str] | None = None
    links: dict[str, str] | None = None
    application_defaults: ApplicationDefaults | None = None
    approval_top_n: int | None = Field(default=None, ge=1, le=50)
    mark_onboarded: bool | None = None


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: DocumentKindLiteral
    label: str
    original_filename: str | None = None
    mime_type: str | None = None
    byte_size: int | None = None
    has_file: bool = False
    text_length: int = 0
    created_at: datetime
    updated_at: datetime


class DocumentTextResponse(BaseModel):
    id: int
    kind: DocumentKindLiteral
    text: str


class ProfileDraft(BaseModel):
    """An un-saved profile, returned by `POST /api/profile/import`.

    Same shape as `ProfileUpdate` but every field is explicitly optional and
    we pair it with a `sources` map so the UI can render "from <source>"
    pills next to pre-filled fields.
    """

    name: str | None = None
    email: str | None = None
    headline: str | None = None
    location: str | None = None
    years_experience: float | None = None
    min_salary: int | None = None
    summary: str | None = None
    target_roles: list[str] = Field(default_factory=list)
    target_locations: list[str] = Field(default_factory=list)
    work_modes: list[WorkModeLiteral] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    links: dict[str, str] = Field(default_factory=dict)
    application_defaults: ApplicationDefaults = Field(default_factory=ApplicationDefaults)


class ProfileImportResponse(BaseModel):
    """The result of `POST /api/profile/import`: a draft + provenance."""

    draft: ProfileDraft
    sources: dict[str, str] = Field(default_factory=dict)
    document_id: int | None = None
    warnings: list[str] = Field(default_factory=list)


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    external_id: str | None
    url: str
    title: str
    company: str | None
    location: str | None
    description: str | None
    match_score: float | None
    match_reason: str | None = None
    created_at: datetime
    application_status: str | None = None


class JobSearchRequest(BaseModel):
    sources: list[str] | None = Field(
        default=None,
        description="Optional board sources to query. Default: none — use inbox sync instead.",
    )
    limit_per_source: int = Field(default=25, ge=1, le=100)
    score: bool = Field(
        default=True,
        description="Run LLM match scoring on jobs returned by this search (re-scores existing listings too).",
    )
    queue_for_approval: bool = Field(
        default=True,
        description="After scoring, promote the top N matches into the approval queue.",
    )
    approval_top_n: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Override profile approval_top_n for this search only.",
    )


class JobSearchResponse(BaseModel):
    sources_queried: list[str]
    jobs_found: int
    jobs_upserted: int
    jobs_scored: int
    approval_queued: int = 0
    warnings: list[str] = Field(default_factory=list)
    jobs: list[JobResponse] = Field(default_factory=list)


class ApplicationResponse(BaseModel):
    id: int
    status: ApplicationStatusLiteral
    job: JobResponse
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class ApplicationDetailResponse(ApplicationResponse):
    resume_doc_id: int | None = None
    cover_doc_id: int | None = None
    submitted_at: datetime | None = None
    resume_preview: str | None = None
    cover_preview: str | None = None
    apply_prepared: bool = False
    apply_meta: dict | None = None


class ApplicationSummary(BaseModel):
    approved: int
    submitted: int
    failed: int


class ApplicationListResponse(BaseModel):
    summary: ApplicationSummary
    items: list[ApplicationDetailResponse] = Field(default_factory=list)


class TailorApplicationResponse(BaseModel):
    application: ApplicationDetailResponse
    warnings: list[str] = Field(default_factory=list)


class PrepareApplyResponse(BaseModel):
    application: ApplicationDetailResponse
    warnings: list[str] = Field(default_factory=list)


class ConfirmSubmitResponse(BaseModel):
    application: ApplicationDetailResponse
    warnings: list[str] = Field(default_factory=list)


class ApprovalSummary(BaseModel):
    top_n: int
    awaiting_approval: int
    approved: int
    rejected: int


class ApprovalListResponse(BaseModel):
    summary: ApprovalSummary
    items: list[ApplicationResponse] = Field(default_factory=list)


class QueueApprovalsRequest(BaseModel):
    top_n: int | None = Field(default=None, ge=1, le=50)


class QueueApprovalsResponse(BaseModel):
    top_n: int
    queued: int
    items: list[ApplicationResponse] = Field(default_factory=list)


EmailProviderLiteral = Literal["disabled", "gmail", "imap"]


class EmailStatusResponse(BaseModel):
    connected: bool
    provider: EmailProviderLiteral
    configured: bool
    connected_email: str | None = None
    gmail_oauth_available: bool = False
    last_sync_at: datetime | None = None
    gmail_history_id: str | None = None
    imap_last_uid: int | None = None
    mailbox: str | None = None


class EmailConnectRequest(BaseModel):
    """Connect an IMAP inbox (credentials stored per user in SQLite)."""

    imap_host: str = Field(min_length=1, max_length=255)
    imap_user: str = Field(min_length=1, max_length=255)
    imap_app_password: str = Field(min_length=1)
    imap_folder: str = Field(default="INBOX", max_length=255)


class EmailConnectResponse(BaseModel):
    ok: bool
    provider: EmailProviderLiteral
    message: str
    connected_email: str | None = None


class EmailGmailAuthorizeResponse(BaseModel):
    authorization_url: str


class EmailGmailSetupResponse(BaseModel):
    configured: bool
    redirect_uri: str
    has_client_id: bool
    setup_steps: list[str] = Field(default_factory=list)
    google_cloud_console_url: str
    gmail_api_enable_url: str
    oauth_clients_url: str


class EmailGmailSetupRequest(BaseModel):
    client_id: str = Field(min_length=8, max_length=512)
    client_secret: str = Field(min_length=8, max_length=512)


class EmailGmailSetupSaveResponse(BaseModel):
    ok: bool
    configured: bool
    message: str


class EmailDisconnectResponse(BaseModel):
    ok: bool = True
    message: str = "Inbox disconnected."


class EmailJobAlertResponse(BaseModel):
    title: str
    company: str | None = None
    location: str | None = None
    url: str
    snippet: str | None = None
    alert_source: str
    email_subject: str = ""
    email_sender: str = ""


class EmailSyncRequest(BaseModel):
    score: bool = Field(
        default=True,
        description="Run match scoring on ingested jobs (snippet used as description when no full JD).",
    )
    queue_for_approval: bool = Field(
        default=True,
        description="After scoring, promote the top N matches into the approval queue.",
    )
    approval_top_n: int | None = Field(
        default=None,
        ge=1,
        le=50,
        description="Override profile approval_top_n for this sync only.",
    )


class EmailSyncResponse(BaseModel):
    messages_scanned: int
    jobs_found: int
    jobs_upserted: int = 0
    jobs_scored: int = 0
    approval_queued: int = 0
    provider: EmailProviderLiteral
    last_sync_at: datetime | None = None
    warnings: list[str] = Field(default_factory=list)
    jobs: list[EmailJobAlertResponse] = Field(default_factory=list)
