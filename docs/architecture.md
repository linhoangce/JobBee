# JobBee — Architecture Notes

## Goals

JobBee is a **consumer application** (not a personal one-off script) that lets a user delegate the tedious parts of a job search to an agent while staying in control of the final submit. The product surface is:

- A normal desktop-feeling UI (React) for onboarding, browsing matched jobs, approving applications, and tracking results.
- A local agent backend (Python) that drives an LLM and a real browser. Everything runs on the user's machine — their resume, cover letter, and credentials never leave their box unless they explicitly use a hosted LLM API.

## Layers

### 1. Frontend — React + Vite + TypeScript

Pages: **Onboarding**, **Job feed**, **Approvals**, **Tracker**, **Settings**. The UI talks to the backend over plain JSON on `/api/*`. The dev server proxies `/api` to FastAPI on `:8000`; in production both will be served from the same origin.

### 2. Backend — FastAPI + SQLAlchemy + SQLite

- **`app.config.Settings`** — typed config from `.env` via `pydantic-settings`. The same fields are also exposed/updatable through `/api/settings` so the UI can reconfigure the LLM without editing files.
- **`app.db`** — a single SQLite file under `backend/data/jobbee.db`. SQLAlchemy 2.x `DeclarativeBase`, sync sessions kept simple for now.
- **`app.models`** — domain entities. Foundation has `User`, `Document` (resume / cover letter), `Job`, `Application`. We deliberately model multi-user even though we only ever expect one local user, so the schema doesn't have to migrate later.
- **`app.llm`** — see below.
- **`app.api.*`** — FastAPI routers, one per concern. Each router is mounted under `/api`.

### 3. LLM abstraction layer

The agents must be portable across local (Ollama) and hosted (OpenAI-compatible) models. `app.llm.LLMProvider` is the seam:

```python
class LLMProvider(Protocol):
    name: str
    default_model: str

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict | None = None,
    ) -> ChatResponse: ...
```

Concrete implementations:

- **`OpenAICompatProvider`** — calls `POST {base_url}/chat/completions`. Works against OpenAI, OpenRouter, Groq, Together, and Gemini's OpenAI-compat endpoint.
- **`OllamaProvider`** — calls `POST {base_url}/api/chat`. No API key, expects the model to have been pulled via `ollama pull`.

Selection happens once at app startup via `get_llm_provider()`, but every agent call accepts a `provider` override so we can mix models per task (e.g. cheap-and-fast for matching, stronger for resume tailoring).

### 4. Agent layer

Lives in `app.agents.*`:

- **`search_agent`** — pulls listings from **safe public APIs** (RemoteOK, Remotive). LinkedIn automated scraping is **deliberately disabled** (ToS violation + aggressive bot detection / account bans). Rate-limited sequential fetches; optional LLM scoring on new rows only.
- **`match_agent`** — scores a JD against profile + resume excerpt (0–100 + reason).
- `tailor_agent` — rewrites resume and cover letter for a specific JD *(next)*.
- `apply_agent` — fills application forms, attaches tailored docs, stops at the final confirm step *(next)*.
- `notify_agent` — Telegram/Discord notifications *(later)*.

#### Job sources policy

| Source | v1 status | Why |
| --- | --- | --- |
| RemoteOK | ✅ enabled | Public JSON API, remote tech jobs |
| Remotive | ✅ enabled | Public JSON API |
| LinkedIn | ❌ blocked | Prohibits automation; CAPTCHA / restriction risk |
| Indeed | ❌ blocked | Similar automation restrictions |
| Greenhouse/Lever URLs | 🔜 planned | User-pasted job URLs, public board pages |

LinkedIn later (if ever): **assisted browse only** — user searches in their own browser, pastes job URLs; JobBee extracts JD text. No headless login scraping.

## Local-first by design

- SQLite means user data never has to leave the machine.
- The Ollama path means the LLM doesn't either.
- The only optional egress is the hosted LLM API and the actual job-board sites.
- Job-site accounts (Workday tenants, iCIMS portals…) are made by the apply agent with the user's email and a password JobBee generates. The password lives only in the OS keyring (`site:<host>`; the `site_accounts` table is the index). The driver types it into password inputs on that host; it never reaches the model, logs or events. Settings shows it on an explicit click. JobBee never asks for the user's own passwords.

## Open questions for later milestones

- Where to host the bundled Ollama runtime in a packaged build (Tauri? Electron? plain installer?).
- How aggressively to throttle to avoid LinkedIn rate-limiting / soft bans. **Decision:** skip LinkedIn automation in v1; use public APIs + manual URL paste instead.
- Whether to keep tailored resumes as Markdown + render to PDF, or edit user-uploaded `.docx` directly.
- Multi-account support (the schema allows it; the UX doesn't yet).
