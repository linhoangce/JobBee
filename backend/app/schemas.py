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
    "filtered_out",
    "awaiting_approval",
    "approved",
    "awaiting_submit",
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
    llm_provider: Literal["anthropic", "openai", "ollama"]
    llm_base_url: str
    llm_model: str
    llm_temperature: float
    llm_timeout_s: float
    has_api_key: bool


class SettingsUpdate(BaseModel):
    llm_provider: Literal["anthropic", "openai", "ollama"] | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    llm_timeout_s: float | None = Field(default=None, gt=0.0, le=600.0)


class LLMTestRequest(BaseModel):
    """Optional fields override saved settings without persisting them."""

    provider: Literal["anthropic", "openai", "ollama"] | None = None
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
    is_base: bool = True
    application_id: int | None = None
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








# --- Claude apply runs ---

ApplyRunStatusLiteral = Literal[
    "queued",
    "starting",
    "running",
    "needs_user",
    "needs_human",
    "needs_you",
    "awaiting_submit",
    "revising",
    "submitting",
    "submitted",
    "submit_unconfirmed",
    "discarded",
    "expired",
    "blocked",
    "failed",
    "cancelled",
]
ApplyEffortLiteral = Literal["low", "medium", "high", "xhigh", "max"]


class ApplyRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    application_id: int
    status: ApplyRunStatusLiteral
    url: str
    ats: str
    model: str
    effort: str
    pending: dict | None = None
    attended: bool = False
    finish: dict | None = None
    error: str | None = None
    iterations: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    guard_trips: int = 0
    uninspected_clicks: int = 0
    review: dict | None = None
    answers: list | None = None
    expires_at: datetime | None = None
    confirmation: dict | None = None
    created_at: datetime
    finished_at: datetime | None = None


class ApplyEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    kind: str
    data: dict
    created_at: datetime


class ApplyRunDetailResponse(BaseModel):
    run: ApplyRunResponse
    events: list[ApplyEventResponse] = Field(default_factory=list)


class StartApplyRunRequest(BaseModel):
    model: str | None = None
    effort: ApplyEffortLiteral | None = None


class AnswerChange(BaseModel):
    field: str = Field(min_length=1, max_length=300)
    value: str = Field(max_length=2000)


class ApplyRunReviseRequest(BaseModel):
    changes: list[AnswerChange] = Field(min_length=1, max_length=20)


class ApplyRunResumeRequest(BaseModel):
    note: str = ""


class ApplyRunDecisionRequest(BaseModel):
    submit: bool


class ApplyAgentConfigResponse(BaseModel):
    api_key_configured: bool
    models: list[str]
    default_model: str
    efforts: list[str]
    default_effort: str
    max_cost_usd: float


# --- Home (inbox feed) ---

ApplicationGroupLiteral = Literal["applied", "in_progress", "skipped", "not_a_fit", "didnt_finish"]


class JobCardInfo(BaseModel):
    id: int
    title: str
    company: str | None = None
    location: str | None = None
    work_mode: str | None = None
    salary_text: str | None = None
    url: str


class MatchCard(BaseModel):
    type: Literal["match"] = "match"
    application_id: int
    job: JobCardInfo
    score: int | None = None
    decision: str | None = None
    summary: str | None = None
    reasons: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    resume_doc_id: int | None = None
    cover_doc_id: int | None = None
    style: str | None = None
    can_restyle: bool = False
    prepared_at: datetime | None = None
    older_resume: bool = False  # tailored from the resume the user has since replaced


class RunCard(BaseModel):
    """A run waiting on the user: review (Submit?), handoff (attended runs), set_aside (needs
    the user at their PC some time) or unconfirmed."""

    type: Literal["review", "handoff", "set_aside", "unconfirmed"]
    run_id: int
    application_id: int
    job: JobCardInfo
    handoff_kind: str | None = None
    updating: bool = False  # answers being changed on the parked form
    error: str | None = None
    message: str | None = None
    summary: str | None = None
    checks: list[str] = Field(default_factory=list)
    answers: list[dict] = Field(default_factory=list)
    screenshot_url: str | None = None
    expires_at: datetime | None = None
    clicked: bool | None = None


class PreparingCard(BaseModel):
    type: Literal["preparing"] = "preparing"
    application_id: int
    job: JobCardInfo
    stage: str


class NoticeCard(BaseModel):
    type: Literal["notice"] = "notice"
    id: int
    text: str
    level: str
    application_id: int | None = None
    retry: bool = False
    created_at: datetime


class ApplyingRow(BaseModel):
    run_id: int
    application_id: int
    job: JobCardInfo
    status: str
    last_event: dict | None = None
    started_at: datetime


class ApplicationRow(BaseModel):
    id: int
    job: JobCardInfo
    status: ApplicationStatusLiteral
    group: ApplicationGroupLiteral
    label: str
    stage_reason: str | None = None
    score: int | None = None
    updated_at: datetime
    submitted_at: datetime | None = None


class LastSearch(BaseModel):
    trigger: str
    status: str
    found: int
    kept: int
    sent: int
    error: str | None = None
    started_at: datetime
    finished_at: datetime | None = None


class AgentStatus(BaseModel):
    paused: bool
    searching: bool
    search_stage: str | None = None
    next_search_at: datetime | None = None
    last_search: LastSearch | None = None
    looking_for: str
    sent_today: int
    jobs_per_day: int
    shortlisted: int
    spend_today_usd: float
    daily_budget_usd: float
    budget_reached: bool


class SetupStatus(BaseModel):
    claude_key: bool
    resume: bool
    onboarded: bool
    telegram: bool
    inbox: bool


class HomeResponse(BaseModel):
    setup: SetupStatus
    agent: AgentStatus
    needs_you: list[RunCard | MatchCard | NoticeCard | PreparingCard] = Field(default_factory=list)
    applying: list[ApplyingRow] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    applications: list[ApplicationRow] = Field(default_factory=list)


class JobLinkRequest(BaseModel):
    url: str = Field(min_length=8, max_length=2048)


class JobLinkResponse(BaseModel):
    application_id: int
    status: ApplicationStatusLiteral
    duplicate: bool
    processing: bool
    message: str


class SkipRequest(BaseModel):
    reason: Literal["not_interested", "level", "location", "salary", "company"] | None = None


class ActionResponse(BaseModel):
    ok: bool = True
    message: str = ""
    run_id: int | None = None


class RunSummary(BaseModel):
    id: int
    status: str
    created_at: datetime
    finished_at: datetime | None = None
    cost_usd: float = 0.0
    iterations: int = 0
    error: str | None = None
    summary: str | None = None
    attended: bool = False
    answers: list[dict] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    screenshot_url: str | None = None


class SiteAccountView(BaseModel):
    id: int
    host: str
    login: str
    status: str
    created_at: datetime
    last_used_at: datetime | None = None


class SiteAccountSecret(BaseModel):
    password: str


class ApplicationView(BaseModel):
    id: int
    status: ApplicationStatusLiteral
    group: ApplicationGroupLiteral | None = None
    label: str
    stage_reason: str | None = None
    job: JobCardInfo
    description: str | None = None
    posted_at: datetime | None = None
    discovered_via: str | None = None
    screen: dict | None = None
    changes: list[str] = Field(default_factory=list)
    removed_claims: list[str] = Field(default_factory=list)
    resume_doc_id: int | None = None
    cover_doc_id: int | None = None
    style: str | None = None
    can_restyle: bool = False
    runs: list[RunSummary] = Field(default_factory=list)
    can_retry: bool = False
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None


class PreferencesPayload(BaseModel):
    """What the agent looks for; every field optional on update."""

    target_roles: list[str] | None = None
    target_locations: list[str] | None = None
    work_modes: list[WorkModeLiteral] | None = None
    min_salary: int | None = Field(default=None, ge=0)
    work_authorization: WorkAuthorizationLiteral | None = None
    requires_sponsorship: YesNoLiteral | None = None
    jobs_per_day: int | None = Field(default=None, ge=1, le=25)
    min_match_score: int | None = Field(default=None, ge=0, le=100)
    excluded_companies: list[str] | None = None
    discovery_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_job_age_days: int | None = Field(default=None, ge=1, le=120)
    web_search_enabled: bool | None = None
    document_style: str | None = Field(default=None, max_length=40)
    agent_accounts: bool | None = None
    agent_consents: bool | None = None


class DocumentStyleInfo(BaseModel):
    id: str
    name: str
    description: str


class StyleRequest(BaseModel):
    style: str = Field(max_length=40)


class ClaudeKeyRequest(BaseModel):
    """A new key, a workspace for an organization-wide key, or both."""

    api_key: str | None = Field(default=None, min_length=20, max_length=300)
    workspace_id: str | None = Field(default=None, max_length=100, pattern=r"^[A-Za-z0-9_-]*$")


class ClaudeStatus(BaseModel):
    connected: bool
    source: Literal["app", "env", "none"]
    workspace_id: str = ""
    models: dict[str, str]
    spend_today_usd: float
    spend_month_usd: float
    daily_budget_usd: float
    monthly_budget_usd: float


class BudgetUpdate(BaseModel):
    daily_budget_usd: float | None = Field(default=None, ge=0, le=1000)
    monthly_budget_usd: float | None = Field(default=None, ge=0, le=10000)


class WatchlistItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ats: str
    slug: str
    careers_url: str | None = None
    last_polled_at: datetime | None = None
    last_error: str | None = None


class WatchlistAddRequest(BaseModel):
    company: str = Field(min_length=2, max_length=500)
