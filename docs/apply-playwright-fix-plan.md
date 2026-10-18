# Playwright Apply Fix — Handoff for Next Session

**Date:** 2026-06-12  
**Status:** Fixes 1, 2, 3, 5 implemented (2026-06-12)  
**Do not use Firecrawl for this** — Playwright already reaches the target page; the failure is in harness orchestration.

---

## Observed failure (eBay / external apply)

After LinkedIn external Apply navigation, Playwright lands on the company careers page (e.g. `jobs.ebayinc.com`). Session screenshot:

- **File:** `backend/data/screenshots/app_66_1781295330.png`
- Chatbot overlay is **gone** (overlay dismiss / vision partially worked)
- **"Apply Now"** button is visible in the right sidebar
- Agent **does not click Apply** and does not advance to the application form

This is not a Playwright capability gap. It is a logic bug in how we prioritize heuristics vs vision on external sites.

---

## Root cause

### 1. Heuristics skipped on external apply (primary bug)

In [`apply_agent.py`](../backend/app/agents/apply_agent.py) line ~494:

```python
prefer_vision=apply_flow == "external_apply",
```

In [`browser_harness.py`](../backend/app/agents/browser_harness.py) lines 457–463:

```python
if not prefer_vision:
    for name in _FLOW_BUTTON_NAMES:
        ...
    if not advanced:
        advanced = _try_click_apply_on_job_page(page)
```

When `apply_flow == "external_apply"`, **`prefer_vision=True`** and the harness **never runs** `_try_click_apply_on_job_page()`, which already includes `"Apply Now"` in `_APPLY_ENTRY_NAMES`.

Vision becomes the only path to click Apply. If vision returns `done`, `none`, or fails to parse, the loop breaks with the job description page unchanged — exactly what the screenshot shows.

### 2. No scroll-into-view before Apply click

eBay (and many career sites) place **Apply** in a sticky right sidebar. Buttons may exist in DOM but not be clickable until scrolled into the viewport. Current `_try_click_locator` checks `is_visible()` but does not call `scroll_into_view_if_needed()`.

### 3. Vision may declare `done` on job description page

Vision prompt says `done` when "form looks ready for human review." The model may treat "chatbot closed, page looks clean" as `done` even though Apply was never clicked. Harness continues on `done` (good) but if the next step also fails, we eventually break without advancing.

### 4. Session traces may be empty

If the loop breaks on step 0, check `backend/data/screenshots/sessions/{application_id}/`. Empty or missing traces suggest the harness exited before vision ran or session saving failed — worth verifying in the failing run.

---

## Recommended fixes (in priority order)

### Fix 1 — Always run Apply heuristics (critical, ~15 lines)

**Goal:** Heuristics first, vision as fallback — on all flows including `external_apply`.

**Option A (simplest):** Remove `prefer_vision` gating of heuristics entirely.

In `browser_harness.py`, change the loop to **always** run flow buttons + Apply heuristics before vision:

```python
# Always try heuristics first
for name in _FLOW_BUTTON_NAMES:
    if _click_flow_button(page, name):
        advanced = True
        break
if not advanced:
    advanced = _try_click_apply_on_job_page(page)

# Vision only when heuristics did not advance
if not advanced and context.vision_enabled:
    ...
```

**Option B:** Keep `prefer_vision` but only use it to **reorder** (e.g. dismiss + vision for overlays first), not to skip Apply heuristics.

**Also update** `apply_agent.py`: either remove `prefer_vision=apply_flow == "external_apply"` or document that it no longer skips Apply clicks.

**Files:**
- `backend/app/agents/browser_harness.py` — `BrowserHarness.run_until_ready`
- `backend/app/agents/apply_agent.py` — `_advance_application_form` call site

---

### Fix 2 — Scroll into view before clicking Apply (high impact)

In `_try_click_locator` or `_try_click_apply_on_job_page`, before click:

```python
target.scroll_into_view_if_needed(timeout=3_000)
target.click(timeout=timeout)
```

Consider scrolling the page once before Apply attempts on external career pages:

```python
page.evaluate("window.scrollTo(0, 0)")  # top
# try Apply in sidebar
page.evaluate("window.scrollBy(0, Math.round(window.innerHeight * 0.5))")  # mid-page
```

**Files:**
- `backend/app/agents/browser_harness.py` — `_try_click_locator`, optionally `_try_click_apply_on_job_page`

---

### Fix 3 — Tighten vision prompt for job description vs form (medium)

In `apply_vision_agent.py` system prompt, add explicit rule:

> If the page is a **job description** (title, location, "Apply Now" / "Apply" visible) and no application form fields are open, you MUST click Apply — do **not** return `done`.

Also add negative example: sidebar widgets ("Save Job", "Get Started", job alerts) are not the primary Apply action.

**Files:**
- `backend/app/agents/apply_vision_agent.py` — `_SYSTEM`

---

### Fix 4 — Page-phase detection (optional, reduces vision cost)

Before calling vision, detect page phase with cheap DOM checks:

| Phase | Signals | Action |
|-------|---------|--------|
| `job_posting` | H1 job title + Apply button, few/no text inputs | Click Apply only |
| `application_form` | Multiple inputs, Next/Continue | Fill + advance |
| `blocked` | Large overlay/chat visible | Dismiss first |

Skip vision on `job_posting` if heuristics + scroll fix Apply (Fix 1 + 2).

**Files:**
- New helper in `browser_harness.py`: `_detect_page_phase(page) -> str`

---

### Fix 5 — Better logging in warnings (debuggability)

Append to `warnings[]` each step:

- `"Heuristic: tried Apply Now — not found"` / `"Heuristic: clicked Apply Now"`
- `"Vision returned: click Apply Now"` / `"Vision returned: done (continuing)"`
- `"Step N: no advance, breaking"`

Ensures Tracker UI shows why prepare stopped without opening session JSON.

**Files:**
- `backend/app/agents/browser_harness.py`

---

## What NOT to do

| Approach | Why skip |
|----------|----------|
| **Firecrawl for apply** | Does not fix "click Apply"; adds cost, remote login, resume upload friction |
| **Replace Playwright entirely** | Local persistent profile already handles LinkedIn; screenshot proves navigation works |
| **ATS-specific adapters (yet)** | Generic Apply heuristics + scroll should fix eBay; add Greenhouse/Workday only if needed |

**Stagehand** (`page.act("click Apply Now")`) is a reasonable *second* upgrade if Fixes 1–3 are insufficient — still local Playwright, smarter targeting.

---

## Test plan

### Unit tests (mock Playwright)

Add/update in `backend/tests/test_browser_harness.py`:

1. **`test_external_apply_runs_heuristics_when_prefer_vision`** — `prefer_vision=True` still calls `_try_click_apply_on_job_page`
2. **`test_apply_click_scrolls_into_view`** — mock verifies `scroll_into_view_if_needed` before click
3. **`test_job_posting_does_not_stop_on_vision_done`** — `done` without submit continues loop

### Manual smoke test (eBay)

1. Approve a LinkedIn job with external Apply → eBay careers URL
2. Tracker → Tailor → **Prepare form**
3. Expected: browser clicks **Apply Now**, application form opens (may be new tab)
4. Check warnings for heuristic/vision step log
5. Check `backend/data/screenshots/sessions/{application_id}/step_*.json`
6. Final screenshot should show form fields, not job description only

### Regression

Run existing suite:

```bash
cd backend
.\.venv\Scripts\python.exe -m pytest tests/test_browser_harness.py tests/test_apply_agent.py tests/test_apply_vision_agent.py -q
```

---

## Implementation checklist (copy into next session)

- [ ] **Fix 1:** Remove or invert `prefer_vision` skip of Apply heuristics in `browser_harness.py`
- [ ] **Fix 2:** Add `scroll_into_view_if_needed` before Apply/flow button clicks
- [ ] **Fix 3:** Update vision system prompt — no `done` on job description pages
- [ ] **Fix 5:** Step-level warning messages for Tracker debugging
- [ ] Add unit tests for Fix 1 and Fix 2
- [ ] Manual smoke test on eBay external apply from LinkedIn
- [ ] (Optional) Fix 4 page-phase detection if eBay still fails

---

## Key files reference

| File | Role |
|------|------|
| `backend/app/agents/apply_agent.py` | LinkedIn Easy/external Apply start; sets `prefer_vision` |
| `backend/app/agents/browser_harness.py` | Observe→plan→act loop; overlay dismiss; heuristics vs vision |
| `backend/app/agents/apply_vision_agent.py` | Vision LLM prompt + action schema |
| `backend/app/config.py` | `APPLY_HARNESS_*`, `APPLY_VISION_*` settings |
| `backend/data/screenshots/` | Final screenshot per prepare |
| `backend/data/screenshots/sessions/{id}/` | Per-step harness trace |

---

## Architecture unchanged

Keep current human-in-the-loop flow:

1. User approves job → Tailor → Prepare form  
2. Playwright (headed) fills form, stops before Submit  
3. User reviews in browser, closes window  
4. User confirms submit in Tracker  

Only the **harness decision order** and **click reliability** need fixing — not the overall pipeline or browser technology.
