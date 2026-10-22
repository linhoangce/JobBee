// Typed client for the JobBee backend. Everything goes through `/api/*` (proxied by Vite in dev).

export type WorkMode = "remote" | "hybrid" | "onsite";
export type WorkAuthorization = "yes_no_restrictions" | "yes_with_restrictions" | "no";
export type YesNo = "yes" | "no";

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
}>;

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

export type DocumentInfo = {
  id: number;
  kind: "resume" | "cover_letter";
  label: string;
  original_filename: string | null;
  has_file: boolean;
  text_length: number;
  is_base: boolean;
  application_id: number | null;
  created_at: string;
  updated_at: string;
};

// --- home -----------------------------------------------------------------------

export type ApplicationStatus =
  | "matched"
  | "filtered_out"
  | "awaiting_approval"
  | "approved"
  | "awaiting_submit"
  | "submitted"
  | "rejected_by_user"
  | "failed";

export type ApplicationGroup = "applied" | "in_progress" | "skipped" | "not_a_fit" | "didnt_finish";

export type JobInfo = {
  id: number;
  title: string;
  company: string | null;
  location: string | null;
  work_mode: string | null;
  salary_text: string | null;
  url: string;
};

/** Where an answer came from; "judgement" and "consent" are the agent's own calls. */
export type AnswerSource =
  | "profile"
  | "resume"
  | "standard_answer"
  | "saved_answer"
  | "judgement"
  | "consent"
  | "you";

export type FilledField = { field: string; value: string; source?: AnswerSource; note?: string | null };

export type MatchCard = {
  type: "match";
  application_id: number;
  job: JobInfo;
  score: number | null;
  decision: string | null;
  summary: string | null;
  reasons: string[];
  gaps: string[];
  changes: string[];
  removed_claims: string[];
  resume_doc_id: number | null;
  cover_doc_id: number | null;
  style: string | null;
  can_restyle: boolean;
  prepared_at: string | null;
  older_resume: boolean;
};

export type RunCard = {
  type: "review" | "handoff" | "set_aside" | "unconfirmed";
  run_id: number;
  application_id: number;
  job: JobInfo;
  handoff_kind: string | null;
  updating: boolean;
  error: string | null;
  message: string | null;
  summary: string | null;
  checks: string[];
  answers: FilledField[];
  screenshot_url: string | null;
  expires_at: string | null;
  clicked: boolean | null;
};

export type PreparingCard = {
  type: "preparing";
  application_id: number;
  job: JobInfo;
  stage: string;
};

export type NoticeCard = {
  type: "notice";
  id: number;
  text: string;
  level: "error" | "info";
  application_id: number | null;
  retry: boolean;
  created_at: string;
};

export type Card = MatchCard | RunCard | PreparingCard | NoticeCard;

export type ApplyEvent = {
  id: number;
  kind: string;
  data: Record<string, unknown>;
  created_at: string;
};

export type ApplyingRow = {
  run_id: number;
  application_id: number;
  job: JobInfo;
  status: string;
  last_event: { kind: string; data: Record<string, unknown> } | null;
  started_at: string;
};

export type ApplicationRow = {
  id: number;
  job: JobInfo;
  status: ApplicationStatus;
  group: ApplicationGroup;
  label: string;
  stage_reason: string | null;
  score: number | null;
  updated_at: string;
  submitted_at: string | null;
};

export type LastSearch = {
  trigger: string;
  status: string;
  found: number;
  kept: number;
  sent: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type AgentStatus = {
  paused: boolean;
  searching: boolean;
  search_stage: string | null;
  next_search_at: string | null;
  last_search: LastSearch | null;
  looking_for: string;
  sent_today: number;
  jobs_per_day: number;
  shortlisted: number;
  spend_today_usd: number;
  daily_budget_usd: number;
  budget_reached: boolean;
};

export type SetupStatus = {
  claude_key: boolean;
  resume: boolean;
  onboarded: boolean;
  telegram: boolean;
  inbox: boolean;
};

export type Home = {
  setup: SetupStatus;
  agent: AgentStatus;
  needs_you: Card[];
  applying: ApplyingRow[];
  counts: Record<ApplicationGroup | "needs_you", number>;
  applications: ApplicationRow[];
};

export type ActionResult = { ok: boolean; message: string; run_id: number | null };

export type JobLinkResult = {
  application_id: number;
  status: ApplicationStatus;
  duplicate: boolean;
  processing: boolean;
  message: string;
};

export type SkipReason = "not_interested" | "level" | "location" | "salary" | "company";

export type RunSummary = {
  id: number;
  status: string;
  created_at: string;
  finished_at: string | null;
  cost_usd: number;
  iterations: number;
  error: string | null;
  summary: string | null;
  attended: boolean;
  answers: FilledField[];
  checks: string[];
  screenshot_url: string | null;
};

export type ApplicationView = {
  id: number;
  status: ApplicationStatus;
  group: ApplicationGroup | null;
  label: string;
  stage_reason: string | null;
  job: JobInfo;
  description: string | null;
  posted_at: string | null;
  discovered_via: string | null;
  screen: { decision?: string; score?: number; summary?: string; reasons?: string[]; gaps?: string[] } | null;
  changes: string[];
  removed_claims: string[];
  resume_doc_id: number | null;
  cover_doc_id: number | null;
  style: string | null;
  can_restyle: boolean;
  runs: RunSummary[];
  can_retry: boolean;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
};

export type RunDetail = {
  run: { id: number; status: string; cost_usd: number; iterations: number; error: string | null };
  events: ApplyEvent[];
};

export type SiteAccount = {
  id: number;
  host: string;
  login: string;
  status: string;
  created_at: string;
  last_used_at: string | null;
};

// --- settings ---------------------------------------------------------------------

export type Preferences = {
  target_roles: string[];
  target_locations: string[];
  work_modes: WorkMode[];
  min_salary: number | null;
  work_authorization: WorkAuthorization | null;
  requires_sponsorship: YesNo | null;
  jobs_per_day: number;
  min_match_score: number;
  excluded_companies: string[];
  discovery_time: string;
  max_job_age_days: number;
  web_search_enabled: boolean;
  document_style: string;
  agent_accounts: boolean;
  agent_consents: boolean;
};

export type DocumentStyle = { id: string; name: string; description: string };

export type ClaudeStatus = {
  connected: boolean;
  source: "app" | "env" | "none";
  workspace_id: string;
  models: Record<string, string>;
  spend_today_usd: number;
  spend_month_usd: number;
  daily_budget_usd: number;
  monthly_budget_usd: number;
};

export type WatchlistItem = {
  id: number;
  name: string;
  ats: string;
  slug: string;
  careers_url: string | null;
  last_polled_at: string | null;
  last_error: string | null;
};

export type EmailStatus = {
  connected: boolean;
  provider: "disabled" | "gmail" | "imap";
  configured: boolean;
  connected_email: string | null;
  gmail_oauth_available: boolean;
  last_sync_at: string | null;
};

export type MessagingStatus = {
  channel: string;
  bot_configured: boolean;
  bot_username: string | null;
  linked: boolean;
  linked_at: string | null;
  polling: boolean;
  last_error: string | null;
  paused: boolean;
};

export type MessagingLink = { bot_username: string; link_url: string };

// --- transport ----------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

function detailMessage(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail) && parsed.detail.length > 0) {
      const first = parsed.detail[0] as { msg?: string; loc?: unknown[] };
      const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : null;
      return field ? `${String(field)}: ${first.msg}` : String(first.msg);
    }
  } catch {
    // not JSON
  }
  if (status === 502 || status === 503 || status === 504) {
    return "JobBee's backend isn't responding. Is it running?";
  }
  return body.trim().slice(0, 200) || `Request failed (${status}).`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string> | undefined) };
  if (init?.body && typeof init.body === "string" && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  let resp: Response;
  try {
    resp = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError("Can't reach JobBee's backend. Is it running?", 0);
  }
  if (!resp.ok) {
    const text = await resp.text().catch(() => "");
    throw new ApiError(detailMessage(resp.status, text), resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body ?? {}) });
const put = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PUT", body: JSON.stringify(body) });

export const api = {
  // home + agent
  home: () => request<Home>("/api/home"),
  searchNow: () => post<ActionResult>("/api/agent/search"),
  prepareMore: (count = 3) => post<ActionResult>("/api/agent/more", { count }),
  pause: () => post<ActionResult>("/api/agent/pause"),
  resume: () => post<ActionResult>("/api/agent/resume"),
  dismissNotice: (id: number) => post<ActionResult>(`/api/agent/notices/${id}/dismiss`),
  retryNotice: (id: number) => post<ActionResult>(`/api/agent/notices/${id}/retry`),
  addLink: (url: string) => post<JobLinkResult>("/api/jobs/link", { url }),

  // applications
  getApplication: (id: number) => request<ApplicationView>(`/api/applications/${id}`),
  apply: (id: number) => post<ActionResult>(`/api/applications/${id}/apply`),
  skip: (id: number, reason?: SkipReason) =>
    post<ActionResult>(`/api/applications/${id}/skip`, reason ? { reason } : {}),
  prepare: (id: number) => post<ActionResult>(`/api/applications/${id}/prepare`),
  retry: (id: number) => post<ActionResult>(`/api/applications/${id}/retry`),
  restyle: (id: number, style: string) => post<ActionResult>(`/api/applications/${id}/style`, { style }),
  documentUrl: (docId: number) => `/api/documents/${docId}/file`,
  documentStyles: () => request<DocumentStyle[]>("/api/documents/styles"),
  stylePreviewUrl: (style: string, kind: "resume" | "cover_letter") =>
    `/api/documents/styles/${style}/preview?kind=${kind}`,

  // apply runs
  getRun: (runId: number, afterId = 0) =>
    request<RunDetail>(`/api/apply-runs/${runId}?after_id=${afterId}`),
  /** Every event of a run (the API pages them 200 at a time). */
  getRunEvents: async (runId: number) => {
    const events: ApplyEvent[] = [];
    for (;;) {
      const page = await request<RunDetail>(
        `/api/apply-runs/${runId}?after_id=${events.length ? events[events.length - 1].id : 0}`,
      );
      events.push(...page.events);
      if (page.events.length < 200) return events;
    }
  },
  handoffDone: (runId: number, note = "") => post<unknown>(`/api/apply-runs/${runId}/resume`, { note }),
  attend: (runId: number) => post<unknown>(`/api/apply-runs/${runId}/attend`),
  dismissRun: (runId: number) => post<unknown>(`/api/apply-runs/${runId}/dismiss`),
  revise: (runId: number, changes: { field: string; value: string }[]) =>
    post<unknown>(`/api/apply-runs/${runId}/revise`, { changes }),
  cancelRun: (runId: number) => post<unknown>(`/api/apply-runs/${runId}/cancel`),
  focusRun: (runId: number) => post<void>(`/api/apply-runs/${runId}/focus`),
  decide: (runId: number, submit: boolean) => post<unknown>(`/api/apply-runs/${runId}/decision`, { submit }),
  outcome: (runId: number, wentThrough: boolean) =>
    post<unknown>(`/api/apply-runs/${runId}/outcome`, { went_through: wentThrough }),

  // setup + profile
  setup: () => request<SetupStatus>("/api/setup"),
  finishSetup: () => post<ActionResult>("/api/setup/finish"),
  claude: () => request<ClaudeStatus>("/api/setup/claude"),
  saveClaudeKey: ({ apiKey, workspaceId }: { apiKey?: string; workspaceId?: string }) =>
    put<ClaudeStatus>("/api/setup/claude-key", { api_key: apiKey, workspace_id: workspaceId }),
  forgetClaudeKey: () => request<ClaudeStatus>("/api/setup/claude-key", { method: "DELETE" }),
  saveBudget: (body: { daily_budget_usd?: number; monthly_budget_usd?: number }) =>
    put<ClaudeStatus>("/api/setup/budget", body),
  getProfile: () => request<Profile>("/api/profile"),
  updateProfile: (body: ProfileUpdate) => put<Profile>("/api/profile", body),
  importProfile: (resumeFile: File) => {
    const form = new FormData();
    form.append("resume_file", resumeFile);
    return request<ProfileImportResponse>("/api/profile/import", { method: "POST", body: form });
  },
  listDocuments: () => request<DocumentInfo[]>("/api/documents"),
  getPreferences: () => request<Preferences>("/api/preferences"),
  savePreferences: (body: Partial<Preferences>) => put<Preferences>("/api/preferences", body),

  // job-site accounts the agent made
  siteAccounts: () => request<SiteAccount[]>("/api/site-accounts"),
  revealSiteAccount: (id: number) =>
    post<{ password: string }>(`/api/site-accounts/${id}/reveal`),
  forgetSiteAccount: (id: number) => request<void>(`/api/site-accounts/${id}`, { method: "DELETE" }),

  // sources
  watchlist: () => request<WatchlistItem[]>("/api/watchlist"),
  addCompany: (company: string) => post<WatchlistItem>("/api/watchlist", { company }),
  removeCompany: (id: number) => request<void>(`/api/watchlist/${id}`, { method: "DELETE" }),
  getEmailStatus: () => request<EmailStatus>("/api/email/status"),
  startGmailConnect: () =>
    request<{ authorization_url: string }>("/api/email/gmail/authorize?popup=1"),
  disconnectEmail: () => post<unknown>("/api/email/disconnect"),

  // Telegram (optional)
  getMessagingStatus: () => request<MessagingStatus>("/api/messaging/status"),
  setupTelegram: (token: string) => post<MessagingLink>("/api/messaging/telegram", { token }),
  newTelegramLink: () => post<MessagingLink>("/api/messaging/telegram/link"),
  sendTestMessage: () => post<void>("/api/messaging/test"),
  disconnectTelegram: () => request<void>("/api/messaging/telegram", { method: "DELETE" }),
};
