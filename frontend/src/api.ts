// Tiny typed wrapper around fetch for the JobBee backend.
// Everything goes through `/api/*` and is proxied by Vite in dev.

export type Health = {
  status: "ok";
  app_env: string;
  version: string;
  db: boolean;
  llm_provider: string;
  llm_model: string;
};

export type Settings = {
  llm_provider: "openai" | "ollama";
  llm_base_url: string;
  llm_model: string;
  llm_temperature: number;
  llm_timeout_s: number;
  apply_vision_enabled: boolean;
  apply_vision_model: string;
  has_api_key: boolean;
};

export type SettingsUpdate = Partial<{
  llm_provider: "openai" | "ollama";
  llm_base_url: string;
  llm_api_key: string;
  llm_model: string;
  llm_temperature: number;
  llm_timeout_s: number;
  apply_vision_enabled: boolean;
  apply_vision_model: string;
}>;

export type LLMTestRequest = {
  provider?: "openai" | "ollama";
  base_url?: string;
  api_key?: string;
  model?: string;
  prompt?: string;
};

export type LLMTestResponse = {
  ok: boolean;
  provider: string;
  model: string;
  content?: string | null;
  error?: string | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
};

export type WorkMode = "remote" | "hybrid" | "onsite";
export type WorkAuthorization = "yes_no_restrictions" | "yes_with_restrictions" | "no";
export type YesNo = "yes" | "no";
export type DocumentKind = "resume" | "cover_letter";

export type ApplicationDefaults = {
  phone: string | null;
  phone_extension: string | null;
  phone_device_type: string | null;
  country_phone_code: string | null;
  address_line1: string | null;
  address_line2: string | null;
  city: string | null;
  province: string | null;
  postal_code: string | null;
  country: string | null;
  work_country: string | null;
  work_authorization: WorkAuthorization | null;
  requires_sponsorship: YesNo | null;
  referral_source: string | null;
  willing_to_relocate: YesNo | null;
  saved_answers: Record<string, string>;
};

export type Profile = {
  id: number;
  name: string;
  email: string | null;
  headline: string | null;
  location: string | null;
  years_experience: number | null;
  min_salary: number | null;
  summary: string | null;
  target_roles: string[];
  target_locations: string[];
  work_modes: WorkMode[];
  skills: string[];
  links: Record<string, string>;
  application_defaults: ApplicationDefaults;
  approval_top_n: number;
  onboarded_at: string | null;
  is_complete: boolean;
};

export type ProfileUpdate = Partial<{
  name: string;
  email: string;
  headline: string;
  location: string;
  years_experience: number;
  min_salary: number;
  summary: string;
  target_roles: string[];
  target_locations: string[];
  work_modes: WorkMode[];
  skills: string[];
  links: Record<string, string>;
  application_defaults: Partial<ApplicationDefaults>;
  mark_onboarded: boolean;
  approval_top_n: number;
}>;

export type Document = {
  id: number;
  kind: DocumentKind;
  label: string;
  original_filename: string | null;
  mime_type: string | null;
  byte_size: number | null;
  has_file: boolean;
  text_length: number;
  created_at: string;
  updated_at: string;
};

export type DocumentText = {
  id: number;
  kind: DocumentKind;
  text: string;
};

export type ProfileDraft = {
  name?: string | null;
  email?: string | null;
  headline?: string | null;
  location?: string | null;
  years_experience?: number | null;
  min_salary?: number | null;
  summary?: string | null;
  target_roles: string[];
  target_locations: string[];
  work_modes: WorkMode[];
  skills: string[];
  links: Record<string, string>;
  application_defaults: ApplicationDefaults;
};

export type ProfileImportResponse = {
  draft: ProfileDraft;
  sources: Record<string, string>;
  document_id: number | null;
  warnings: string[];
};

export type ImportProfileInput = {
  resumeFile?: File;
  githubUsername?: string;
  githubToken?: string;
};

export type Job = {
  id: number;
  source: string;
  external_id: string | null;
  url: string;
  title: string;
  company: string | null;
  location: string | null;
  description: string | null;
  match_score: number | null;
  match_reason: string | null;
  created_at: string;
  application_status: string | null;
};

export type JobSearchRequest = {
  sources?: string[];
  limit_per_source?: number;
  score?: boolean;
  queue_for_approval?: boolean;
  approval_top_n?: number;
};

export type JobSearchResponse = {
  sources_queried: string[];
  jobs_found: number;
  jobs_upserted: number;
  jobs_scored: number;
  approval_queued: number;
  warnings: string[];
  jobs: Job[];
};

export type ApplicationStatus =
  | "matched"
  | "awaiting_approval"
  | "approved"
  | "submitted"
  | "rejected_by_user"
  | "failed";

export type Application = {
  id: number;
  status: ApplicationStatus;
  job: Job;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type ApplicationDetail = Application & {
  resume_doc_id: number | null;
  cover_doc_id: number | null;
  submitted_at: string | null;
  resume_preview: string | null;
  cover_preview: string | null;
  apply_prepared: boolean;
  apply_meta: Record<string, unknown> | null;
};

export type ApplicationSummary = {
  approved: number;
  submitted: number;
  failed: number;
};

export type ApplicationListResponse = {
  summary: ApplicationSummary;
  items: ApplicationDetail[];
};

export type TailorApplicationResponse = {
  application: ApplicationDetail;
  warnings: string[];
};

export type PrepareApplyResponse = {
  application: ApplicationDetail;
  warnings: string[];
};

export type ConfirmSubmitResponse = {
  application: ApplicationDetail;
  warnings: string[];
};

export type ApprovalSummary = {
  top_n: number;
  awaiting_approval: number;
  approved: number;
  rejected: number;
};

export type ApprovalListResponse = {
  summary: ApprovalSummary;
  items: Application[];
};

export type QueueApprovalsRequest = {
  top_n?: number;
};

export type QueueApprovalsResponse = {
  top_n: number;
  queued: number;
  items: Application[];
};

export type JobSourcesInfo = {
  available: string[];
  blocked: string[];
  notes: Record<string, string>;
};

export type EmailProvider = "disabled" | "gmail" | "imap";

export type EmailStatus = {
  connected: boolean;
  provider: EmailProvider;
  configured: boolean;
  connected_email: string | null;
  gmail_oauth_available: boolean;
  last_sync_at: string | null;
  gmail_history_id: string | null;
  imap_last_uid: number | null;
  mailbox: string | null;
};

export type EmailConnectImapRequest = {
  imap_host: string;
  imap_user: string;
  imap_app_password: string;
  imap_folder?: string;
};

export type EmailConnectResponse = {
  ok: boolean;
  provider: EmailProvider;
  message: string;
  connected_email?: string | null;
};

export type EmailGmailAuthorizeResponse = {
  authorization_url: string;
};

export type EmailJobAlert = {
  title: string;
  company: string | null;
  location: string | null;
  url: string;
  snippet: string | null;
  alert_source: string;
  email_subject: string;
  email_sender: string;
};

export type EmailSyncRequest = {
  score?: boolean;
  queue_for_approval?: boolean;
  approval_top_n?: number;
};

export type EmailSyncResponse = {
  messages_scanned: number;
  jobs_found: number;
  jobs_upserted: number;
  jobs_scored: number;
  approval_queued: number;
  provider: EmailProvider;
  last_sync_at: string | null;
  warnings: string[];
  jobs: EmailJobAlert[];
};

/** Normalize legacy / partial backend payloads so UI never crashes on missing fields. */
function normalizeEmailSyncResponse(raw: Record<string, unknown>): EmailSyncResponse {
  const messagesScanned =
    typeof raw.messages_scanned === "number"
      ? raw.messages_scanned
      : typeof raw.messages_fetched === "number"
        ? raw.messages_fetched
        : 0;
  const jobs = Array.isArray(raw.jobs) ? (raw.jobs as EmailJobAlert[]) : [];
  const warnings = Array.isArray(raw.warnings) ? (raw.warnings as string[]) : [];
  const jobsFound =
    typeof raw.jobs_found === "number" ? raw.jobs_found : jobs.length;

  return {
    messages_scanned: messagesScanned,
    jobs_found: jobsFound,
    jobs_upserted: typeof raw.jobs_upserted === "number" ? raw.jobs_upserted : 0,
    jobs_scored: typeof raw.jobs_scored === "number" ? raw.jobs_scored : 0,
    approval_queued: typeof raw.approval_queued === "number" ? raw.approval_queued : 0,
    provider: (raw.provider as EmailProvider) ?? "disabled",
    last_sync_at: typeof raw.last_sync_at === "string" ? raw.last_sync_at : null,
    warnings,
    jobs,
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
  // Default Content-Type for JSON requests, but let multipart uploads set their own boundary.
  if (init?.body && typeof init.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(path, { ...init, headers });
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new Error(`${resp.status} ${resp.statusText}: ${text}`);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<Health>("/api/health"),

  getSettings: () => request<Settings>("/api/settings"),
  updateSettings: (body: SettingsUpdate) =>
    request<Settings>("/api/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    }),

  testLLM: (body: LLMTestRequest) =>
    request<LLMTestResponse>("/api/llm/test", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  pingLLM: () =>
    request<LLMTestResponse>("/api/llm/ping", {
      method: "POST",
      body: "{}",
    }),

  getProfile: () => request<Profile>("/api/profile"),
  updateProfile: (body: ProfileUpdate) =>
    request<Profile>("/api/profile", {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  importProfile: async ({ resumeFile, githubUsername, githubToken }: ImportProfileInput) => {
    const form = new FormData();
    if (resumeFile) form.append("resume_file", resumeFile);
    if (githubUsername) form.append("github_username", githubUsername);
    if (githubToken) form.append("github_token", githubToken);
    return request<ProfileImportResponse>("/api/profile/import", {
      method: "POST",
      body: form,
    });
  },

  listDocuments: () => request<Document[]>("/api/documents"),
  uploadDocument: async (file: File, kind: DocumentKind, label?: string) => {
    const form = new FormData();
    form.append("file", file);
    form.append("kind", kind);
    if (label) form.append("label", label);
    return request<Document>("/api/documents", { method: "POST", body: form });
  },
  pasteDocument: (kind: DocumentKind, text: string, label?: string) =>
    request<Document>("/api/documents/text", {
      method: "POST",
      body: JSON.stringify({ kind, text, label }),
    }),
  getDocumentText: (id: number) => request<DocumentText>(`/api/documents/${id}/text`),
  deleteDocument: (id: number) =>
    request<void>(`/api/documents/${id}`, { method: "DELETE" }),

  getJobSources: () => request<JobSourcesInfo>("/api/jobs/sources"),
  listJobs: (minScore?: number) =>
    request<Job[]>(minScore != null ? `/api/jobs?min_score=${minScore}` : "/api/jobs"),
  searchJobs: (body: JobSearchRequest = {}) =>
    request<JobSearchResponse>("/api/jobs/search", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listApprovals: () => request<ApprovalListResponse>("/api/approvals"),
  queueApprovals: (body: QueueApprovalsRequest = {}) =>
    request<QueueApprovalsResponse>("/api/approvals/queue", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  approveApplication: (id: number) =>
    request<Application>(`/api/approvals/${id}/approve`, { method: "POST" }),
  rejectApplication: (id: number) =>
    request<Application>(`/api/approvals/${id}/reject`, { method: "POST" }),

  listApplications: (status?: ApplicationStatus) =>
    request<ApplicationListResponse>(
      status ? `/api/applications?status=${status}` : "/api/applications",
    ),
  getApplication: (id: number) => request<ApplicationDetail>(`/api/applications/${id}`),
  tailorApplication: (id: number) =>
    request<TailorApplicationResponse>(`/api/applications/${id}/tailor`, { method: "POST" }),
  prepareApplication: (id: number) =>
    request<PrepareApplyResponse>(`/api/applications/${id}/prepare-apply`, { method: "POST" }),
  confirmSubmit: (id: number) =>
    request<ConfirmSubmitResponse>(`/api/applications/${id}/confirm-submit`, { method: "POST" }),
  markApplicationFailed: (id: number, reason?: string) =>
    request<ApplicationDetail>(
      reason
        ? `/api/applications/${id}/mark-failed?reason=${encodeURIComponent(reason)}`
        : `/api/applications/${id}/mark-failed`,
      { method: "POST" },
    ),
  applicationScreenshotUrl: (id: number) => `/api/applications/${id}/screenshot`,

  getEmailStatus: () => request<EmailStatus>("/api/email/status"),
  startGmailConnect: (popup = false) =>
    request<EmailGmailAuthorizeResponse>(
      popup ? "/api/email/gmail/authorize?popup=1" : "/api/email/gmail/authorize",
    ),
  connectImap: (body: EmailConnectImapRequest) =>
    request<EmailConnectResponse>("/api/email/connect/imap", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  disconnectEmail: () =>
    request<{ ok: boolean; message: string }>("/api/email/disconnect", { method: "POST" }),
  syncEmailAlerts: async (body?: EmailSyncRequest) =>
    normalizeEmailSyncResponse(
      (await request<Record<string, unknown>>("/api/email/sync", {
        method: "POST",
        body: JSON.stringify(body ?? {}),
      })) as Record<string, unknown>,
    ),
};
