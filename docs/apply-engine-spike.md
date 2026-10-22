# Apply engine spike (Phase 1): runbook

**Goal:** before building the production apply engine, measure whether Claude with Anthropic's browser toolset (`browser_toolset_20260801`) fills real applications reliably. The spike also measures the cost and records what each ATS sends on final submit. The result is a go/no-go decision for Phase 2.

This supersedes `apply-playwright-fix-plan.md`, `handoff-workday-modal-stuck.md` and `session-handoff-2026-06-12.md`. Those describe the retired heuristic/vision harness.

## Testing from the UI (recommended)

1. Start the backend **without `--reload`**. A reload restarts the server and interrupts a run.
   From `backend/`: `.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000`
2. Start the frontend. From `frontend/`: `npm.cmd run dev`. Then open http://localhost:5173/tracker (the **Apply** tab).
3. Add a posting with **Add a job by URL**, or use a job you already approved.
4. On its card, pick a model and click **Apply with Claude**. Chrome opens on the posting with JobBee's own profile; the first time, you'll sign in to LinkedIn or Workday when the card asks.
5. While the run is going:
   - Questions Claude can't answer appear on the card. Answers are saved to your profile for next time.
   - "Claude needs you" cards are hand-offs. Do the step in Chrome, then click **Done — continue**.
   - The step log shows what Claude is doing, including any submit attempts JobBee blocked.
6. At the review step, check the form in Chrome and submit it there yourself if it's right. Then click **I submitted it** or **Not submitting — close run**, with your accuracy score.

UI runs land in the same scorecard as the script runs (`run_id` = `ui-<n>`), so `scripts\apply_spike.py --report` covers both. One run is open at a time.

## What the spike runs

`backend/scripts/apply_spike.py`:

- Opens **your own Google Chrome**, headed, with a dedicated JobBee profile at `backend/data/browser-chrome/`, on the job posting.
- Waits while you sign in to anything you need (LinkedIn, Workday, …). The profile keeps those sessions for later runs.
- Runs Claude through the SDK tool runner with Browser Use's toolset driver, wrapped by **JobBee's submit guard** (`app/apply/spike_driver.py`, `app/apply/guards.py`). The guard refuses:
  - clicks, by element ref or by coordinate, on final-submit controls
  - Enter in fields of an application form
  - Enter inside embedded frames it can't inspect

  Site search boxes, sign-in forms and wizard buttons (Next, Continue, Review) stay allowed. `javascript_exec` and raw mouse-down/up are disabled. Uploads are confined to the run's staging folder.
- Builds the agent's context from:
  - your JobBee profile and standard answers
  - answers you gave in earlier spike runs (`data/spike/saved_answers.json`)
  - the base resume text, and the base resume **file**, which it uploads
  - an ATS playbook (`app/apply/playbooks/*.md`)
- Gives Claude, besides the browser:
  - **`site_account`**: fills the email and a generated password on sign-up, sign-in and new-password forms (saved per site in Windows Credential Manager).
  - **`check_inbox`**: opens the site's verification or reset link, or returns its code, from your connected inbox.
  - **`request_human_action`**: for CAPTCHAs, phone codes or a blocked click that is really an intermediate step. In the terminal you're there, so it waits for you.
- Claude answers questions itself; answers it decided are marked "judgement".
- Ends when Claude calls **`finish`** at the review page. You check the browser, answer three questions, and optionally submit yourself. Your submit is recorded so we learn that ATS's final-submit endpoint.

## Setup (once)

1. Add your key to `backend/.env`: `ANTHROPIC_API_KEY=sk-ant-...`
2. Make sure your base resume is uploaded in JobBee (Onboarding), as a PDF. Or pass `--resume path\to\resume.pdf`.
3. Google Chrome must be installed. The script finds `%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe` or Program Files.

On first run, the script also brings your database schema up to date. It applies the same migrations the backend runs on startup, and moves your Gmail token into Windows Credential Manager. A backup from before these changes is in `backend/data/_backup/2026-10-18/`.

## Running it

From `backend/`:

```powershell
# one or more postings, default model (APPLY_MODEL, Opus 5.5)
.\.venv\Scripts\python.exe scripts\apply_spike.py --url https://job-boards.greenhouse.io/acme/jobs/123

# same posting set on other models, for the cost comparison
.\.venv\Scripts\python.exe scripts\apply_spike.py --url <url> --model claude-sonnet-5-5
.\.venv\Scripts\python.exe scripts\apply_spike.py --url <url> --model claude-haiku-5-5

# a job already in JobBee (adds its description to the agent's context)
.\.venv\Scripts\python.exe scripts\apply_spike.py --job-id 40

# scorecard so far
.\.venv\Scripts\python.exe scripts\apply_spike.py --report
```

Useful flags:

| Flag | Purpose |
|---|---|
| `--effort low\|medium\|high` | Thinking effort. |
| `--max-cost 3` | Per-application stop, in estimated USD. Default `APPLY_MAX_COST_USD`, which is 5. |
| `--max-iterations 120` | Iteration cap for the run. |

Each run starts with a tiny preflight request. If the model rejects the request shape, you find out before Chrome opens.

**Ctrl+C** aborts a run without scoring it. If you'd rather keep the result, let the agent finish and answer "n" to the first question.

## Posting mix (about 10)

| ATS | Count | Notes |
|---|---|---|
| Greenhouse | 2 | `job-boards.greenhouse.io/...`. Include one embedded on a company site if you can. |
| Lever | 1 | `jobs.lever.co/...` |
| Ashby | 1 | `jobs.ashbyhq.com/...` |
| LinkedIn Easy Apply | 2 | You'll sign in to LinkedIn on the first run. |
| Workday | 2 | Include the BMO posting from June: `bmo.wd3.myworkdayjobs.com/.../R260015725`. The agent creates the Workday account and confirms it from your inbox. |
| Long tail | 1–2 | iCIMS, SuccessFactors, a custom careers site. |

Use postings you'd really apply to. Submitting is your choice per run; the agent never submits. LinkedIn: keep it to a handful of runs a day.

## What gets recorded

Each run writes `backend/data/spike/runs/<timestamp>_<ats>/`:

| File | Contents |
|---|---|
| `transcript.jsonl` | Every model turn: tool calls, token usage, guard blocks, questions, human hand-offs. |
| `summary.json` | The scorecard row for this run. |
| `submit_candidates.json` | POST/PUT requests made after the agent stopped, i.e. by your submit. Used to build the Phase 2 network-level guard. |
| `network.har` | HTTPS traffic of the first tab, without response bodies. **Request bodies include the form data you submitted**, so it stays local. `backend/data/` is gitignored. |
| `uploads/` | The staged resume copy the agent was allowed to upload. |

`backend/data/spike/scorecard.jsonl` holds one line per run; `--report` prints it.

## Go / no-go (review together before Phase 2)

| Criterion | Target |
|---|---|
| Reached the review page, form filled | ≥ 80% on Greenhouse, Lever, Ashby and LinkedIn Easy Apply |
| Workday | Gets past Apply → Apply Manually and the account step by itself |
| Agent submissions | **0** |
| Field accuracy | You judge it per run. Look for invented facts, and for wrong answers to screening questions. |
| Cost per application | Compare Opus 5.5, Sonnet 5.5 and Haiku 5.5 on the same postings, then choose `APPLY_MODEL`. |

**Also note:**
- Guard false positives: blocked clicks that were really intermediate steps.
- Uninspected clicks inside cross-origin iframes. The spike driver can't classify these; the Phase 2 Playwright driver will.
- Any page where the agent got lost.

These feed the playbooks and the Phase 2 driver.

## Known spike limitations (fixed in Phase 2)

- Clicks inside **cross-origin iframes** can't be classified by the spike guard. They are counted as `uninspected_clicks`, and you are watching. Enter inside such frames is refused.
- No network-level submit block yet. The spike collects the endpoints to block.
- The Browser Use driver is pinned through a small compatibility shim. Browser Use 0.13.11 targets pre-release SDK names (see `app/apply/spike_driver.py`).
- Saved answers are a local JSON file. Phase 2 moves them into the `SavedAnswer` table and the Settings UI.
