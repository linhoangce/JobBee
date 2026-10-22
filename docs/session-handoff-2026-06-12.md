# JobBee Session Handoff — 2026-06-12

> **Partly superseded (2026-10-18):** the apply-harness sections describe the retired heuristic/vision engine; see [apply-engine-spike.md](apply-engine-spike.md). The application-defaults and resume-import notes still apply.

**Purpose:** Continue apply automation, ATS form filling, and onboarding import without re-discovering context.

---

## What was fixed this session

### 1. Playwright external apply harness (eBay bug)

**Problem:** After LinkedIn → external careers page, chatbot was dismissed but **Apply Now** was never clicked. Agent stopped on job description page.

**Root cause:** `prefer_vision=True` for `external_apply` skipped Apply heuristics entirely. Vision alone had to click Apply; `done`/`none` stopped the loop.

**Implemented:**
- Always run flow buttons + `_try_click_apply_on_job_page()` **before** vision (`browser_harness.py`)
- Removed `prefer_vision` parameter from harness / `apply_agent.py`
- `scroll_into_view_if_needed()` before Apply/flow clicks (sidebar buttons)
- Tighter vision prompt: no `done` on job description pages (`apply_vision_agent.py`)
- Step-level warnings for Tracker debugging (`Step N: heuristics clicked…`, `vision returned…`)

**Not done:**
- [ ] Manual smoke test: LinkedIn → eBay external Apply → Prepare form → confirm Apply Now opens form
- [ ] (Optional) Page-phase detection to skip unnecessary vision calls

**Key files:** `backend/app/agents/browser_harness.py`, `apply_agent.py`, `apply_vision_agent.py`  
**Prior doc:** `docs/apply-playwright-fix-plan.md` (checklist partially updated)

---

### 2. ATS screening questions (application defaults)

**Problem:** Dropdowns/radios (work authorization, sponsorship, “How did you hear about…?”) were not auto-filled — filler only handled text inputs and skipped `select`/`radio`.

**Implemented:**
- `users.application_defaults` JSON column on `User` model
- `ApplicationDefaults` schema + profile API (`GET`/`PUT /api/profile`)
- Label → canonical key matching in `backend/app/services/application_defaults.py`
- Extended `_fill_application_fields` for `<select>`, radio groups, phone
- **Application preferences** UI card in Onboarding (`frontend/src/pages/Onboarding.tsx`)

**Default values (when unset):**
- `work_authorization`: `yes_no_restrictions`
- `requires_sponsorship`: `no`
- `referral_source`: `LinkedIn Job Posting` (also inferred from LinkedIn job URL)
- `work_country`: inferred from profile `location` (e.g. Toronto, ON → Canada)

**Not done:**
- [ ] Auto-save `saved_answers` when user fills unknown questions manually during Prepare
- [ ] Signup flow for ATS sites requiring account creation (email + generated password, persist credentials)

**Key files:** `application_defaults.py`, `apply_agent.py`, `models.py`, `schemas.py`, `api/profile.py`, `Onboarding.tsx`, `api.ts`

**Verify:** Restart backend (schema adds column; dev DB auto-migrates on stale schema). Set preferences in Onboarding, then Prepare form on PocketHealth-style ATS.

---

### 3. Resume import JSON parsing (Gemma vs Qwen)

**Problem:** Resume import failed with Gemma 4: log showed valid-looking truncated JSON but raised “model didn't return JSON we could parse.” Qwen3 worked.

**Root causes:**
1. JSON wrapped entirely in `<thought>...</thought>` was stripped to empty string by `strip_model_artifacts`
2. Truncated JSON mid-string needed salvage + partial success, not hard fail

**Implemented:**
- Shared parser: `backend/app/llm/json_utils.py` (`extract_json_object`, truncation salvage, thought-block extraction)
- `strip_model_artifacts` preserves JSON inside `<thought>` when payload starts with `{`
- Import succeeds if **any** meaningful field salvaged (`name`, `email`, `headline`, `summary`, `skills`, `target_roles`)
- Auto-retry once when truncated and no meaningful fields (compact prompt, `max_tokens=8192`)
- Shorter summary in prompt (≤240 chars) to reduce truncation

**Key files:** `llm/json_utils.py`, `llm/base.py`, `importers/resume.py`  
**Tests:** `tests/test_llm_json_utils.py`, updated `tests/test_importers.py`

**Verify:** Re-import resume with Gemma 4 in Settings; should reach Review with pre-filled fields even if truncated.

---

## Current human-in-the-loop apply flow (unchanged)

1. Approve job → Tailor → **Prepare form** (headed Playwright)
2. Harness: dismiss overlays → fill fields → heuristics Apply → vision fallback
3. User reviews in browser, closes window
4. User confirms submit in Tracker

---

## Test commands

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_browser_harness.py tests/test_apply_agent.py tests/test_application_defaults.py tests/test_importers.py tests/test_llm_json_utils.py tests/test_profile.py -q
```

```powershell
cd frontend
npm run build
```

---

## Suggested next session priorities

| Priority | Task | Why |
|----------|------|-----|
| **P0** | Manual smoke: eBay external apply from LinkedIn | Confirm harness Fix 1+2 in real browser |
| **P0** | Manual smoke: PocketHealth / dropdown ATS form | Confirm application defaults fill selects/radios |
| **P1** | Signup helper for ATS account creation | User mentioned jobs requiring signup; email + dummy password + save |
| **P1** | Learn `saved_answers` from manual fills during Prepare | Long-tail company-specific questions |
| **P2** | Page-phase detection in harness | Reduce vision cost on simple job posting pages |
| **P2** | Use `json_utils.extract_json_object` in `match_agent.py` / `tailor_agent.py` | Consolidate duplicate salvage logic |

---

## Key paths reference

| Path | Role |
|------|------|
| `backend/app/agents/browser_harness.py` | Observe→plan→act loop; heuristics before vision |
| `backend/app/agents/apply_agent.py` | LinkedIn Easy/external start; form field filling |
| `backend/app/services/application_defaults.py` | ATS question label matching |
| `backend/app/importers/resume.py` | Resume → profile LLM extraction |
| `backend/app/llm/json_utils.py` | Model-agnostic JSON extraction |
| `backend/data/screenshots/sessions/{application_id}/` | Harness step traces |
| `frontend/src/pages/Onboarding.tsx` | Profile + application preferences |

---

## Known environment notes

- **OS:** Windows 10, PowerShell (use `;` not `&&` in shell)
- **LLM:** User on Gemma 4 via OpenAI-compatible API (Settings); Qwen3 worked for resume import before
- **DB:** SQLite, single local user `id=1`; schema drift triggers drop+recreate in dev (`db.py`)
- **Do not commit** unless user asks

---

## Architecture doc

See `docs/architecture.md` for overall pipeline. This handoff covers **implementation deltas** since last stable behavior, not full system design.
