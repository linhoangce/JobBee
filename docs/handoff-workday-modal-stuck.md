# Handoff — Workday “Start Your Application” modal stuck

**Date:** 2026-06-12  
**Status:** Open (P0)  
**Application under test:** BMO ML/AI Research Engineer (`application_id=40`)  
**URL:** `https://bmo.wd3.myworkdayjobs.com/en-US/External/job/Toronto-ON-CAN/ML-AI-Research-Engineer_R260015725?source=Social_Linkedin`

---

## Bug summary

After LinkedIn **external Apply** opens the BMO Workday job page, automation reaches the **“Start Your Application”** modal but **does not click through to the form**. The browser is left on the modal; Prepare form finishes with the user staring at **Apply Manually** / Autofill / Apply With LinkedIn.

**Expected:** Click **Apply** → wait for modal → click **Apply Manually** → application form opens.  
**Actual:** Step 0 clicks Apply (`advanced: true`). Step 1 sees modal buttons but **`advanced: false`**; harness stops. Final screenshot still shows the modal.

**Vision is not required** for this flow — it is a deterministic Workday pattern. Vision is **disabled by default** (`APPLY_VISION_ENABLED=false`).

---

## Repro

1. Restart backend (pick up harness changes).
2. Tracker → job with LinkedIn external apply (BMO posting above).
3. **Prepare form** (headed Playwright).
4. Sign in on LinkedIn if prompted; external Apply opens Workday tab.
5. Observe: modal appears; automation does not proceed past it.

---

## Evidence (latest run, app 40)

| Artifact | What it shows |
|----------|----------------|
| `backend/data/screenshots/sessions/40/step_00.json` | Page loaded; buttons include **Apply**, cookie banner; **`advanced: true`** (Apply clicked) |
| `backend/data/screenshots/sessions/40/step_01.json` | Modal open: **Apply Manually**, Autofill, etc.; **`advanced: false`** |
| `backend/data/screenshots/app_40_1781302152.png` | Final state — still on modal (see attached in IDE) |

Earlier in the same thread (before fixes):

- **Blank white screenshot** at step 0 — harness ran before Workday SPA rendered; quit immediately.
- **Infinite loop** — re-clicked background **Apply** every step; unconditional **Escape** closed/reopened modal; false `advanced: true` without page change.

---

## Root causes identified (cumulative)

| # | Cause | Symptom |
|---|--------|---------|
| 1 | Harness started before Workday SPA rendered | Blank step 0, `visible_buttons: []`, immediate stop |
| 2 | Heuristics re-clicked **Apply** while modal already open | Same screenshot every step, loop |
| 3 | `dismiss_blocking_ui()` pressed **Escape** every step | Modal closed/reopened |
| 4 | Clicks counted as “advanced” without screenshot change | Vision never ran; stuck detection ineffective |
| 5 | Clicks fired before modal fully interactive | Step 1 lists **Apply Manually** but click fails / no navigation |
| 6 | (Separate) Vision path broken for Ollama | Wrong message format + wrong default model — **vision now off** |

---

## What was implemented (still not fully fixing modal)

All in `backend/app/agents/browser_harness.py` unless noted.

- **`wait_for_apply_page_ready()`** — wait for SPA text/buttons before harness loop.
- **`wait_for_application_entry_modal()`** — wait until Apply Manually / Autofill buttons visible, enabled, non-zero size; then **5s settle**.
- **`_APPLICATION_ENTRY_NAMES`** + **`_try_click_application_entry_modal()`** — prefer **Apply Manually**; skip re-clicking Apply when modal open.
- **Workday selectors:** `[data-automation-id="jobPostingApplyButton"]`, `[data-automation-id="applyManually"]` (ids unverified on live DOM).
- **`_MIN_DOM_MS = 5_000`** — minimum 5s settle after clicks; `_ensure_locator_ready()` before every click.
- **`_page_changed()`** — invalidate “advance” if screenshot unchanged after click.
- **Cookie:** explicit `Accept Cookies` selector.
- **`apply_agent.py`:** wait for page ready after external tab navigation; 5s settle after Apply navigation.
- **`playwright_runner.run_coro_sync()`** — fix asyncio loop conflict when vision *was* enabled.
- **Config:** `apply_vision_enabled` default **false**; Ollama vision model resolution fixed (not relevant while vision off).

---

## What is still broken (hypotheses for next engineer)

Step 1 has modal buttons in `visible_buttons` but **`advanced: false`**. Likely one of:

1. **Workday button not matched by Playwright locators** — `get_by_role("button", name="Apply Manually")` and `button:has-text("Apply Manually")` may not hit the real node (shadow DOM, nested spans, wrong role). **`data-automation-id` values may be wrong** — need live DOM inspection.
2. **Click succeeds but `_page_changed()` is too strict** — modal→form transition may take >5s or look similar in viewport screenshot; harness treats as no advance and **stops** (no retry on step 1).
3. **Cookie / overlay intercepting click** — step 0 still had Decline/Accept Cookies; step 1 may need explicit dismiss before modal click.
4. **Two-step loop is wrong shape** — step 0 ends after Apply; step 1 re-observes and tries entry click in a **new** iteration. Better: **single atomic sequence** after Apply: `wait modal → click Apply Manually → wait form` without breaking between harness steps.
5. **Harness exits on first failed advance** — with vision off, step 1 `advanced: false` → `"no advance — stopping harness"` (see `run_until_ready` break logic).

---

## Recommended next steps (priority order)

### P0 — Confirm why Apply Manually click fails

1. Run Prepare form with **`apply_harness_save_session=true`** (default) and add **temporary debug logging** in `_try_click_application_entry_modal`: log each selector attempt and exception message.
2. In headed browser, **DevTools → inspect Apply Manually** on BMO modal: record `data-automation-id`, tag, role, aria-label.
3. Update Workday selectors to match real DOM (common Workday ids: `jobPostingApplyButton`, `applyManually`, `applyWithResume` — verify on site).

### P0 — Fix advance detection for modal → form

- After clicking **Apply Manually**, wait for **form-specific signal** instead of only screenshot hash, e.g.:
  - `[data-automation-id="applyFlowPage"]` or input fields visible
  - modal buttons **gone** from DOM
  - `wait_for_function` checking absence of “Start Your Application”
- Do **not** stop harness on step 1 if modal was open and entry click was attempted — **retry** entry click 2–3 times with settle.

### P1 — Atomic Workday apply sequence

Consider dedicated helper (no vision):

```text
dismiss cookies → click jobPostingApplyButton → wait_for_application_entry_modal
→ click applyManually (verified selector) → wait_for_apply_form_ready
```

Call once from harness when URL contains `myworkdayjobs.com` instead of generic observe→act loop.

### P1 — Playwright trace on failure

On harness stop with `advanced: false`, save `context.tracing` zip under `sessions/40/` for replay.

### P2 — Vision

Leave disabled unless exotic ATS. If re-enabled: Ollama needs `to_ollama_message()` (done); set `APPLY_VISION_MODEL` or rely on `LLM_MODEL` for Ollama.

---

## Key files

| File | Role |
|------|------|
| `backend/app/agents/browser_harness.py` | Harness loop, modal waits, Apply / Apply Manually heuristics |
| `backend/app/agents/apply_agent.py` | LinkedIn → external tab; starts harness on `form_page` |
| `backend/app/agents/playwright_runner.py` | Thread + `run_coro_sync` for async vision |
| `backend/app/config.py` | `apply_vision_enabled` (default false), harness step limits |
| `backend/data/screenshots/sessions/{id}/step_*.json` | Per-step URL, buttons, `advanced` flag |
| `backend/data/screenshots/app_{jobId}_*.png` | Final screenshot after Prepare |

---

## Config (.env)

```env
LLM_PROVIDER=ollama
LLM_MODEL=gemma4-e4b-agent:latest
APPLY_VISION_ENABLED=false
```

Restart backend after `.env` or harness changes (`get_settings()` is cached).

---

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_browser_harness.py tests/test_apply_agent.py tests/test_config.py tests/test_ollama_provider.py -q
```

Tests mock Playwright; **they do not catch live Workday DOM mismatches**. Manual smoke on BMO posting is required.

---

## Acceptance criteria

- [ ] Prepare form on BMO posting: modal opens, **Apply Manually** clicked automatically, multi-step application form visible.
- [ ] Session trace: step after modal shows **`advanced: true`** and form fields (or Next/Continue), not modal buttons.
- [ ] No vision/LLM calls during this flow (`APPLY_VISION_ENABLED=false`).
- [ ] No infinite loop on identical screenshot; no stuck blank white page at step 0.

---

## Related docs

- `docs/session-handoff-2026-06-12.md` — broader session (eBay Apply, ATS defaults, resume JSON).
- `docs/apply-playwright-fix-plan.md` — earlier apply harness plan (partially superseded).
