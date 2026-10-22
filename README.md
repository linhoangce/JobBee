# JobBee

A job-search agent that runs on your PC.
- **Every day it finds jobs that fit you.** It searches job boards, your company watchlist and your LinkedIn alert emails.
- **It keeps only the best matches** and tailors your CV for each one. Your approval comes next.
- **On Apply, Claude fills in the application** in a Chrome window on your PC.
- **You see the filled form before anything is sent.** Nothing is submitted until you click Submit.

> **Status (Oct 2026):**
> - **Built and working:** the whole loop in the browser. That covers the 3-step welcome, the home inbox, daily search, screening, tailored CV PDFs, applying, review, and Submit.
> - **Telegram** is built as an optional phone channel, but it hasn't been live-tested yet.
> - **How to try it:** [`docs/ui-guide.md`](docs/ui-guide.md).

## How it works

```
 sources ──► read posting ──► filters ──► Claude Haiku: fit score ──► best N/day
 (web search on ATS boards,                                            │
  company watchlist, LinkedIn                                          ▼
  alert emails, links you paste)              Claude Sonnet: tailored CV + cover letter (PDF),
                                              fact-checked against your resume
                                                         │
                         Home inbox card  (Apply / Skip) ◄┘   ← tap 1
                                │ Apply
                                ▼
          Claude Opus fills the form in Chrome (browser tool, one job at a time)
          · signs up / in with a saved site account, confirms it from your inbox
          · answers everything itself and flags its own calls
          · a CAPTCHA or phone code sets the job aside for you; the rest carry on
                                │
                Review card: screenshot + every answer, change any (Submit / Don't)   ← tap 2
                                │ Submit
                JobBee clicks the page's one final-submit button, once
```

Safety is enforced in code, not prompts:
- **The agent cannot submit.** Its browser driver blocks every final-submit click and key press.
- **Submit happens only after your click.** It needs the same tab and the same page you reviewed.
- **Job pages and emails are only read by tool-less, schema-checked model calls.** The one exception is the guarded apply agent.
- **ID numbers, bank and card details are never filled.** The driver refuses those fields.
- **Site passwords never reach the model.** JobBee generates them, keeps them in Windows Credential Manager, and types them only into password fields on that same site.
- **Every Claude call is logged with its cost**, and daily and monthly limits stop spending.

## Run it (Windows)

You need:
- Python 3.11+
- Node 20+
- Google Chrome
- An Anthropic API key with some credit

```powershell
# backend
cd backend
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\.venv\Scripts\playwright.exe install chromium
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# frontend (second terminal)
cd frontend
npm.cmd install
npm.cmd run dev
```

Open http://localhost:5173. The welcome flow asks for your Claude key, your resume and what you're looking for, then runs the first search.
- **Where the key lives:** your Claude key and any bot or inbox tokens are stored in Windows Credential Manager, never in `.env` or the database.
- **Run the backend without `--reload`.** A reload restarts the server and drops any application in progress.

## The app

| Page | What it's for |
| --- | --- |
| `/welcome` | Three steps: connect Claude → drop your resume → what you want. |
| `/` | **Agent bar:** what it's looking for, next search, Find jobs now, paste a link, pause. |
| | **Needs you:** new matches with their CV, review and Submit (change any answer first), and jobs set aside for when you're at your PC. |
| | **Applying now**, then **your applications** (Applied, In progress, Skipped, Not a fit, Didn't finish). Click any row for details. |
| `/settings` | Preferences, sources (web search, watchlist, LinkedIn alerts), what the agent may do for you (accounts, consents) and the accounts it made, resume and form answers, Claude key and limits, optional Telegram. |

## Backend map

```
backend/app/
  api/         home (the inbox in one call), agent (search/pause/link), applications,
               apply_runs, setup (Claude key, finish), preferences, watchlist,
               profile, documents, email (Gmail/IMAP connect), messaging (Telegram)
  pipeline/    flow (screen → tailor → card → apply/skip), discovery (daily search),
               scheduler, ingest, jd (posting fetchers), screen, tailor, prefs,
               sources/ (ats_boards, web_search, email_alerts)
  apply/       Claude browser agent: agent loop, guarded driver, guards, run manager
  documents/   CV / cover letter → PDF (Chrome print)
  messaging/   Telegram channel (optional)
  llm/         Claude provider + spend ledger (local OpenAI-compatible/Ollama still work)
  migrations/  Alembic; the database upgrades in place on startup
```

Claude models by task:

| Task | Model |
| --- | --- |
| Screening | Haiku 5.5 |
| Tailoring, job search | Sonnet 5.5 |
| Applying | Opus 5.5 (`APPLY_MODEL`) |

Typical cost is cents per job and about $0.50–$1 per application filled.

## Tests

```powershell
cd backend; .\.venv\Scripts\python.exe -m pytest -q
cd frontend; npm.cmd run build
```

Tests never call a real model or Telegram. One test (`test_spike_driver.py`) drives a real local Chrome.

## Docs

- [`docs/learn/`](docs/learn/README.md): build JobBee yourself from scratch, one stage at a time, with explanations and diagrams.
- [`docs/ui-guide.md`](docs/ui-guide.md): try the whole flow in the browser.
- [`docs/chat-agent.md`](docs/chat-agent.md): optional Telegram channel.
- [`docs/apply-engine-spike.md`](docs/apply-engine-spike.md): the apply agent, its guard, and the CLI spike.
- `docs/architecture.md`, `docs/session-handoff-2026-06-12.md`, `docs/apply-playwright-fix-plan.md`: historical (superseded).

## License

TBD (private project).
