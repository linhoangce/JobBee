# JobBee

An agent-powered consumer app that searches jobs, scores them against your profile, lets you approve the best matches, and (in upcoming phases) tailors documents and applies on your behalf. Humans always stay in the loop on the final submit.

> **Status:** Onboarding, job search, matching, approvals, tailoring, Playwright-assisted apply, and the Apply tracker (`/tracker`) are implemented. **Next:** email job-alert intake, URL paste import, outbound notifications.

## What's implemented

### Foundation

- **FastAPI backend** with typed config (`.env` + `/api/settings`), SQLite via SQLAlchemy, and schema-drift handling in dev.
- **React + Vite + TypeScript frontend** with dev proxy (`/api` → `:8000`).
- **LLM abstraction** (`OpenAICompatProvider` + `OllamaProvider`) used by all agents and importers.
- **Health + LLM ping/test** endpoints and a Settings page to validate provider config.
- **45+ backend tests** (pytest) covering importers, profile, job search, matching, approvals, tailoring, and apply pipeline.

### Onboarding & profile

- **Import-first onboarding** (`/onboarding`): upload resume (PDF/DOCX/txt) and/or import GitHub profile.
- **Resume importer** — LLM extracts structured profile JSON (name, headline, skills, target roles, links, etc.). Handles truncated JSON from slow local models; full resume text up to 20k characters.
- **GitHub importer** — public REST API, no LLM.
- **Merge + provenance** — imported fields win over empty profile fields; UI shows “from resume / github” pills.
- **Profile API** — `GET/PUT /api/profile`, `POST /api/profile/import`.
- **Documents API** — upload, paste text, list, delete resume/cover letter files under `backend/data/uploads/`.
- **Profile fields** — target roles, skills, locations, work modes, `approval_top_n` (default 5).

### Job search & matching

- **Safe public sources only** (no LinkedIn/Indeed scraping — ToS + ban risk):
  - **RemoteOK** — public JSON API
  - **Remotive** — public JSON API
- **Search agent** — queries sources by target role, upserts jobs, creates `Application` rows.
- **Match agent** — LLM scores 0–100 with a rubric (roles, skills, location, work mode) + short reason.
- **Match heuristics** (`match_fit.py`) — deterministic location/work-mode adjustments on top of LLM scores.
- **Hybrid scoring per search run:**
  - Up to **8 new listings** get full LLM scoring (with thinking enabled on Ollama).
  - Remaining jobs use fast keyword + location/work-mode heuristics (no extra API calls).
  - **Re-search** re-ranks all listings with heuristics so profile/location edits update ordering without burning LLM quota.
- **Jobs API** — `GET /api/jobs`, `POST /api/jobs/search`, `GET /api/jobs/sources`.
- **Jobs UI** (`/jobs`) — run search, view match scores/reasons, link to approvals queue.

### Approvals

- **Top-N queue** — after search, the highest-scored `matched` applications move to `awaiting_approval` (configurable `approval_top_n`, default 5).
- **Approvals API** — list queue, rebuild queue, approve, reject.
- **Application statuses** — `matched` → `awaiting_approval` → `approved` / `rejected_by_user` (plus `submitted`, `failed` reserved for apply phase).
- **Approvals UI** (`/approvals`) — review pending jobs, approve/reject, adjust top-N and rebuild queue.

### Tailor & apply pipeline

- **`tailor_agent`** — two-phase LLM calls (resume, then cover letter); JSON salvage + template cover fallback for local models.
- **`apply_agent`** — Playwright opens `job.url`, fills common fields, attaches resume, stops before submit; screenshot + metadata in `Application.notes`.
- **Applications API** — `GET /api/applications`, `POST .../tailor`, `.../prepare-apply`, `.../confirm-submit`, `.../mark-failed`, `GET .../screenshot`.
- **Apply UI** (`/tracker`) — tailor docs → prepare form → human confirm submit.
- **Playwright setup:** `pip install -e .` then `.\.venv\Scripts\playwright.exe install chromium`.

### LLM behavior

| Task | Thinking (Ollama) | Timeout |
| --- | --- | --- |
| Resume import / JSON extraction | Off | `LLM_LONG_TIMEOUT_S` (default 7200s / 2h) |
| Document tailoring | Off | `LLM_LONG_TIMEOUT_S` (resume) + `LLM_TIMEOUT_S` (cover) |
| Job match scoring | On | `LLM_TIMEOUT_S` (default 600s) |
| LLM ping / test | Off | `LLM_TIMEOUT_S` |

**Providers supported:**

| Provider | Config | Notes |
| --- | --- | --- |
| OpenAI | `LLM_PROVIDER=openai`, `LLM_API_KEY` | Any OpenAI-compatible endpoint |
| OpenRouter | `LLM_PROVIDER=openai`, `OPENROUTER_API_KEY` | Auto-used when base URL is OpenRouter |
| Gemini (compat) | `LLM_PROVIDER=openai`, `LLM_API_KEY` | OpenAI-compat layer |
| Ollama (local) | `LLM_PROVIDER=ollama` | No key; e.g. `qwen3-5-9b-agent:latest`, `qwen3.5:9b` |

Cloud models hit rate limits easily on batch scoring; local Ollama avoids quotas but is slower on CPU.

## Architecture

```
React + Vite (frontend)              FastAPI + SQLAlchemy (backend)
  Home / Settings                      Health, settings, LLM test
  Onboarding (import + review)         Profile + documents APIs
  Jobs (search + feed)        <----->  Job search + match agents
  Approvals (approve/reject)           Approvals service + API
  Apply /tracker            <----->  tailor_agent + apply_agent (Playwright)
        \______________________________/
                    HTTP JSON :8000
                         |
              SQLite (backend/data/jobbee.db)
              Ollama / OpenRouter / OpenAI-compat
              RemoteOK + Remotive APIs
              (planned) Gmail/IMAP job-alert emails
```

## Repo layout

```
JobBee/
  backend/
    app/
      api/           health, settings, llm, profile, documents, jobs, approvals, applications
      agents/        search_agent, match_agent, tailor_agent, apply_agent, sources/
      importers/     resume (LLM), github, merge
      llm/           OpenAI-compat + Ollama providers, factory
      services/      approvals, tailoring, apply, text_extract (PDF/DOCX)
      models.py      User, Document, Job, Application
    tests/           pytest suite
    data/            SQLite DB + uploads (gitignored)
  frontend/
    src/pages/       Home, Onboarding, Jobs, Approvals, Tracker, Settings
    src/api.ts       Typed fetch wrapper
  docs/
    architecture.md  Design notes, source policy, agent roadmap
```

## Frontend routes

| Route | Page | Purpose |
| --- | --- | --- |
| `/` | Home | Status, profile summary, LLM ping |
| `/onboarding` | Onboarding | Import resume/GitHub, review, save profile |
| `/jobs` | Jobs | Run job search, browse all matches |
| `/approvals` | Approvals | Review top-N, approve/reject |
| `/tracker` | Apply | Tailor docs, prepare form, confirm submit |
| `/settings` | Settings | LLM provider config |

## API summary

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/api/health` | App + DB + LLM model info |
| GET/PUT | `/api/settings` | Read/update LLM config (persists to `.env`) |
| POST | `/api/llm/ping`, `/api/llm/test` | Connectivity check |
| GET/PUT | `/api/profile` | User profile |
| POST | `/api/profile/import` | Resume + GitHub import → draft |
| GET/POST/DELETE | `/api/documents` | Resume/cover letter storage |
| GET | `/api/jobs/sources` | Available/blocked job sources |
| GET | `/api/jobs` | Job feed with match scores |
| POST | `/api/jobs/search` | Run search agent (+ optional approval queue) |
| GET | `/api/approvals` | Pending approval queue + summary |
| POST | `/api/approvals/queue` | Rebuild top-N queue |
| POST | `/api/approvals/{id}/approve` | Approve a match |
| POST | `/api/approvals/{id}/reject` | Reject a match |
| GET | `/api/applications` | Apply pipeline (approved / submitted / failed) |
| POST | `/api/applications/{id}/tailor` | Generate tailored resume + cover letter |
| POST | `/api/applications/{id}/prepare-apply` | Playwright form prep (stops before submit) |
| POST | `/api/applications/{id}/confirm-submit` | Human-confirmed submit |
| POST | `/api/applications/{id}/mark-failed` | Mark application failed |

Interactive docs: `http://localhost:8000/docs`

## Data model (SQLite)

- **`User`** — profile fields, `approval_top_n`, onboarding timestamp.
- **`Document`** — resume/cover letter (file + extracted text).
- **`Job`** — listing from a source (title, company, location, description, `match_score`, `match_reason`).
- **`Application`** — links user ↔ job with status workflow.

## Prerequisites

- **Python 3.11+** (3.12/3.13 tested)
- **Node 20+** and npm
- **LLM:** hosted OpenAI-compatible API **or** local [Ollama](https://ollama.com) with a model pulled (e.g. `ollama pull qwen3.5:9b`)

## Setup

### 1. Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
Copy-Item .env.example .env
# Edit .env — see LLM configuration below

.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- API: `http://127.0.0.1:8000`
- Health: `GET http://127.0.0.1:8000/api/health`

### 2. Frontend

```powershell
cd frontend
npm.cmd install    # use npm.cmd if PowerShell blocks npm.ps1 (execution policy)
npm.cmd run dev
```

- UI: `http://localhost:5173` (proxies `/api` → backend)

### 3. Ollama (local LLM)

```powershell
ollama serve          # if not already running
ollama pull qwen3.5:9b
```

Example `backend/.env` for local:

```env
LLM_PROVIDER=ollama
LLM_BASE_URL=http://localhost:11434
LLM_MODEL=qwen3-5-9b-agent:latest
LLM_TIMEOUT_S=600
LLM_LONG_TIMEOUT_S=7200
```

Resume import on CPU can take several minutes; the long timeout prevents premature failures.

## Typical user flow

1. **Onboarding** — import resume (+ optional GitHub), review extracted profile, save.
2. **Jobs** — run job search (RemoteOK + Remotive). Top matches are scored and queued.
3. **Approvals** — review top N jobs, approve ones to pursue, reject others.
4. **Apply** (`/tracker`) — tailor docs, prepare form in browser, confirm submit.

## Testing

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q
```

## Next phase (not yet built)

1. **`email_agent`** — job-alert email intake (see implementation plan below).
2. **URL paste import** — user pastes Indeed / Greenhouse / Lever / company career URLs → extract JD → match.
3. **`notify_agent`** — optional **outbound** Telegram/Discord alerts (distinct from email *intake*).
4. **Universal importers** — LinkedIn PDF export, Credly, ORCID.
5. **LinkedIn / Indeed policy** — no automated search/scraping; assisted apply + email/URL intake only.

See `docs/architecture.md` for design rationale and source policy.

---

## Email job alerts intake — implementation plan

Use **job alert emails** (LinkedIn, Indeed, etc.) as a discovery channel instead of scraping those sites. The agent reads **your inbox** via API/IMAP, extracts job URLs + metadata, then reuses the existing `Job` → `match_agent` → approvals → tailor → apply pipeline.

### Design principles

| Do | Don't |
| --- | --- |
| Read inbox via **Gmail API (OAuth)** or **IMAP + app password** | Automate the Gmail/Outlook web UI with Playwright |
| **Parse HTML/text** of alert emails for links, title, company, snippet | Scrape LinkedIn/Indeed search results |
| Upsert `Job` rows with `source="email_linkedin"` (etc.) | Store inbox passwords in plain text (use OAuth tokens / env) |
| Score from email snippet first; optionally enrich JD via Playwright + logged-in browser | Auto-apply without approval |
| Dedupe by canonical job URL (`Job.source` + `Job.external_id` or URL hash) | Re-process the same alert on every poll |

### Target flow

```
Job alert email (LinkedIn / Indeed)
  → email_agent (poll inbox)
  → parser extracts { url, title, company, location, description_snippet }
  → upsert Job + Application (status=matched)
  → match_agent (snippet or enriched JD)
  → optional queue_top_matches → awaiting_approval
  → existing Approvals → Apply (/tracker) pipeline
```

Optional enrichment step (Phase D): if snippet is too short, open `job.url` in Playwright **persistent context** (`backend/data/browser/`) where the user is already logged in, scrape visible JD text only.

### Phase A — Inbox connection (backend foundation)

**Goal:** Connect one inbox, list unread/recent messages, no parsing yet.

1. Add settings to `backend/.env.example`:
   - `EMAIL_PROVIDER=gmail|imap|disabled`
   - Gmail: `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` (OAuth)
   - IMAP: `IMAP_HOST`, `IMAP_USER`, `IMAP_APP_PASSWORD`, `IMAP_FOLDER` (e.g. `INBOX`)
2. Add `backend/app/services/email_client.py` — thin wrapper:
   - Gmail: `google-api-python-client` or `httpx` to Gmail REST (`users.messages.list`, `users.messages.get`)
   - IMAP: stdlib `imaplib` + `email` module
3. Add `backend/app/api/email.py`:
   - `GET /api/email/status` — connected?, provider, last sync time
   - `POST /api/email/connect` — start OAuth flow or save IMAP settings (dev: manual token paste)
   - `POST /api/email/sync` — fetch new messages since cursor, return raw count (debug)
4. Persist sync state in SQLite (new table or `User` JSON field):
   - `email_last_sync_at`, `email_history_id` (Gmail) or `email_uid_validity` + last UID (IMAP)
5. Tests: mock Gmail/IMAP responses; no real network in CI.

**Files to add/change:** `services/email_client.py`, `api/email.py`, `schemas.py`, `models.py` (optional `EmailSyncState`), `main.py`, `pyproject.toml` (deps), `tests/test_email_client.py`.

### Phase B — Alert parsers (per sender template)

**Goal:** Turn raw MIME/HTML into structured job listings.

1. Add `backend/app/agents/email_parsers/`:
   - `base.py` — `ParsedJobAlert` dataclass: `url`, `title`, `company`, `location`, `snippet`, `alert_source`
   - `linkedin.py` — match senders like `jobs-listings@linkedin.com`, `messages-noreply@linkedin.com`
   - `indeed.py` — match `alert@indeed.com` (later)
2. Parser responsibilities:
   - Unwrap tracking URLs (`click.linkedin.com`, `indeed.com/pagead/clk`) → canonical job URL
   - Strip HTML with `html.parser` or `beautifulsoup4` (add dep if needed)
   - Regex fallback for title/company when template changes
3. Unit tests with **fixture HTML** saved under `backend/tests/fixtures/emails/` (copy real alert emails, redact PII).

**Do not** use LLM for parsing in v1 — deterministic parsers are easier to test and debug.

### Phase C — `email_agent` + pipeline integration

**Goal:** Sync inbox → create `Job` + `Application` → score → optional approval queue.

1. Add `backend/app/agents/email_agent.py`:
   - `async def run_email_sync(session, user, *, queue_for_approval: bool) -> EmailSyncResult`
   - For each new message: pick parser by sender → 0..N `ParsedJobAlert` per email
   - Reuse `search_agent._upsert_job` pattern (or extract shared `upsert_job_from_listing()` helper)
   - Set `Job.source` = `email_linkedin` / `email_indeed`; `external_id` = stable id from URL path or hash
   - `_ensure_application` → `ApplicationStatus.matched`
   - Call `match_agent.score_job` (snippet as `job.description` if no full JD yet)
   - Optionally `queue_top_matches` like `POST /api/jobs/search`
2. Add API:
   - `POST /api/email/sync` — run agent, return `{ messages_scanned, jobs_found, jobs_upserted, jobs_scored, approval_queued, warnings }`
   - Mirror `JobSearchResponse` shape where possible
3. Register `email_linkedin` in `GET /api/jobs/sources` as **available** (distinct from blocked `linkedin` scrape source).

**Files:** `agents/email_agent.py`, extend `api/email.py`, `agents/sources/registry.py` or separate `EMAIL_SOURCES` constant.

### Phase D — Optional JD enrichment (Playwright)

**Goal:** When email snippet is short, fetch full description from job URL.

1. Add `backend/app/agents/jd_fetch_agent.py` (or method on `email_agent`):
   - Input: `job.url`, user's Playwright persistent context
   - Output: plain-text JD (truncate to ~6k chars)
   - **Only** after user has logged in manually once; no credential storage
   - Skip / warn on CAPTCHA or login wall
2. Call enrichment before `match_agent` when `len(description) < 200`.
3. Gate behind setting: `EMAIL_ENRICH_JD=true` (default off for v1).

### Phase E — Frontend & settings

**Goal:** User can connect inbox and trigger sync from UI.

1. **Settings** (`/settings`) or new **Email** section:
   - Provider toggle (disabled / Gmail / IMAP)
   - Connect button (OAuth link or IMAP fields)
   - “Sync job alerts now” button
   - Last sync time + last result summary
2. **`frontend/src/api.ts`** — `getEmailStatus`, `syncEmailAlerts`, `connectEmail`
3. **Jobs page** — optional “Sync from email” next to “Run job search”
4. Show provenance on job cards: source badge `email_linkedin`

### Phase F — Scheduling & hardening

1. Background sync: optional APScheduler / FastAPI lifespan task every N minutes (config `EMAIL_POLL_INTERVAL_MINUTES`, default off).
2. Mark processed messages (Gmail label `JobBee/Processed` or IMAP flag) to avoid re-ingestion.
3. Rate limits: max messages per sync, max jobs per run.
4. Logging + `warnings[]` in API response for parse failures.

### Phase G — Indeed + other senders

1. Add `indeed.py` parser using same `ParsedJobAlert` interface.
2. Later: Wellfound, Glassdoor alerts, custom filters by sender whitelist in settings.

### Suggested dependency additions

```toml
# backend/pyproject.toml (optional extras)
google-auth>=2.0
google-auth-oauthlib>=1.0
google-api-python-client>=2.0
# OR imap only: no extra deps (stdlib)
beautifulsoup4>=4.12   # HTML email parsing
```

### Test checklist

- [ ] Gmail client lists messages (mocked)
- [ ] LinkedIn HTML fixture → 1+ `ParsedJobAlert` with canonical URL
- [ ] Tracking URL unwrap resolves to stable job URL
- [ ] Dedupe: same URL twice → one `Job`, one `Application`
- [ ] `POST /api/email/sync` creates jobs and scores them
- [ ] Approval queue promotion works after email sync
- [ ] Enrichment skipped when snippet is long enough

### Handoff notes for the next session

- **Tailor/apply already work** — email intake should only add discovery; wire into existing `Job` / `Application` models.
- **Blocked sources** (`linkedin`, `indeed` in `registry.py`) refer to **search scraping**, not `email_linkedin` intake.
- **Playwright** is installed via `playwright install chromium`; apply uses `backend/data/browser/` persistent profile.
- **Frontend on Windows:** use `npm.cmd` if PowerShell blocks `npm.ps1`.
- **Local LLM tailoring** uses two-phase calls; truncated JSON is salvaged; cover letter has template fallback.

## License

TBD (private project).
