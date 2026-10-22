# Stage 23: The API: actions, apply runs and setup

> **Goal:** Add the last four API files: the actions on one application, the controls for apply runs, first-run setup with the Claude key, and the list of job-site accounts. Then write the final `main.py` and run the whole backend test suite.
>
> **By the end you will have:** the complete backend. Every endpoint the web app calls exists, `main.py` is in its final form, and `python -m pytest -q` reports `218 passed, 4 skipped`. That's **Milestone 1: the backend is complete.**

## Where this fits

Stage 22 gave the home page its data and the agent bar its buttons. The buttons on the cards are still missing: Apply, Skip, Submit, Change answers, I'm at my PC. So are the welcome flow's Claude key box and the settings page's list of site accounts. This stage adds those four API files and the final `main.py` that mounts them. After it, stages 24 to 28 build the web app on top of a finished backend.

```mermaid
flowchart LR
  UI["Web app buttons (stages 24-28)"] --> APPS["api/applications.py"]
  UI --> RUNS["api/apply_runs.py"]
  UI --> SETUP["api/setup.py"]
  UI --> SA["api/site_accounts.py"]
  APPS --> FLOW["job_flow (stage 12)"]
  APPS --> RM["run_manager (stage 21)"]
  RUNS --> RM
  SETUP --> KR[("OS keyring (stage 2)")]
  SETUP --> ANT["Anthropic API"]
  SA --> KR
  style APPS fill:#f0ab1d,color:#000
  style RUNS fill:#f0ab1d,color:#000
  style SETUP fill:#f0ab1d,color:#000
  style SA fill:#f0ab1d,color:#000
```

## New ideas in plain words

### Check who owns it, then act

Every endpoint that takes an id first loads the row and checks that it belongs to the local user. There is one small helper per file: `_owned` for applications, `_get_run` for runs (a run belongs to whoever owns its application) and `_account` for site accounts.

A row that doesn't exist and a row that isn't yours get the same answer: 404 Not Found. A different answer, such as 403 Forbidden, would tell a caller that the id exists. JobBee has one local user today, so the check never fails in normal use. It costs one comparison, and it keeps the API correct if a second user ever appears.

### The core raises Python errors; the API turns them into status codes

The job flow (stage 12) and the run manager (stage 21) know nothing about HTTP. The Telegram buttons from stage 11 call them too, and "409" means nothing in a chat. So they raise plain Python exceptions that say what went wrong, and each endpoint translates them:

| Exception | Raised when | Becomes |
|---|---|---|
| `FlowError` | the card was already handled (stage 12) | 409 Conflict |
| `RunConflictError` | the application already has an open run (stage 21) | 409 Conflict |
| `RunStateError` | the run isn't in a state that accepts this action (stage 21) | 409 Conflict |
| `ValueError` | an unknown model, or an application that isn't approved | 400 Bad Request |
| `LookupError` | the application doesn't exist | 404 (400 in `retry`) |
| `SecretStoreError` | the keyring refused to save (stage 2) | 500 Internal Server Error |

`raise HTTPException(...) from e` keeps the original error attached, which helps when you read a log.

### Double clicks are safe

You might click Apply twice, or tap Apply on your phone after clicking it on the web. Both requests reach the server. The job flow and the run manager change a status with one atomic `UPDATE ... WHERE status = <the expected one>`, then check how many rows changed (stages 12 and 21). The first request changes one row and wins. The second changes none and raises an error, which the API turns into a 409 with a plain message, such as "That one was already handled."

### Paging with `after_id`

One apply run can log hundreds of events. `GET /api/apply-runs/{id}?after_id=N` returns at most 200 events whose id is greater than `N`. To read all of them, a client starts with 0, then asks again with the last id it received, until a page comes back with fewer than 200. To follow a live run, it asks only for events newer than the ones it has.

This is called **cursor paging**: the cursor is the last id you saw. Unlike "page 3 of 10", it never skips or repeats an event when new ones arrive while you read.

### Sending a file instead of JSON

The screenshot endpoint returns a PNG image. FastAPI's `FileResponse(path, media_type="image/png")` sends the file and sets the `Content-Type` header, so the browser shows it as an image. The path comes from the database, so it first goes through `confined_file` from stage 10, which only serves files that sit inside JobBee's own data folders.

### Secrets go in and don't come out

The Claude key travels to the server once, when you save it, and is never sent back. Any response can end up in the browser's developer tools, in a screenshot or screen share, or in a log. The page only needs to know *whether* a key is set and where it came from. So the status the setup endpoints return has `connected` and `source`, and no key. If you lose the key, you make a new one in the Anthropic Console.

There is one deliberate exception: the password of a job-site account that JobBee created for you. Step 4 explains why that's acceptable.

### Who can reach the API: localhost

Every computer has an address for talking to itself: `127.0.0.1`, also called **localhost** or the *loopback* address. When uvicorn starts with `--host 127.0.0.1` (its default), it only accepts connections that come from your own PC. Other devices on your Wi-Fi can't connect at all. Starting it with `--host 0.0.0.0` would accept connections from every network your PC is on, so never do that with JobBee.

## Build it, step by step

### Step 1: Actions on one application, `backend/app/api/applications.py`

This file lives in `backend/app/api/`. It serves the application drawer and the buttons on a match card: Apply, Skip, Update CV and the style menu (stage 27), plus "Fill the form again" in the drawer.

#### Part 1: Imports, the router and the ownership check

- The docstring lists the six endpoints. Every path starts with `/api/applications`, because of `prefix="/applications"` here and `prefix="/api"` in `main.py`.
- The imports reuse what you wrote in stage 22: `_aware`, `can_restyle`, `job_info` and `status_view` from `home.py`, and `require_ready` from `agent.py`. That's why stage 22 came first.
- `OPEN_STATUSES` from stage 21 is the set of run statuses that mean "not finished yet": queued, filling, parked or submitting.
- `_RETRYABLE` holds the two statuses where "fill the form again" makes sense. `failed` means the last run gave up. `approved` covers a run that expired, was stopped or was set aside: the application is still approved, but nothing is working on it.
- `_owned` loads the application and answers 404 if it's missing or not yours.

<!-- code: backend/app/api/applications.py lines=1-39 -->
**`backend/app/api/applications.py`** (lines 1–39 of 190)

```python
"""One application: details for the drawer, and the user's decisions on it.

GET  /api/applications/{id}
POST /api/applications/{id}/apply      Apply on a match card (queues the Claude agent)
POST /api/applications/{id}/skip       {reason?}
POST /api/applications/{id}/prepare    tailor a shortlisted job now, or a card's CV again
POST /api/applications/{id}/retry      fill the form again after a failed or timed-out run
POST /api/applications/{id}/style      {style} render the CV and cover letter in another style
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..apply.runs import OPEN_STATUSES, RunConflictError, run_manager
from ..db import get_session
from ..documents.render_pdf import STYLES
from ..models import Application, ApplicationStatus, ApplyRun, Job
from ..pipeline.flow import FlowError, job_flow
from ..schemas import ActionResponse, ApplicationView, RunSummary, SkipRequest, StyleRequest
from .agent import require_ready
from .home import _aware, can_restyle, job_info, status_view
from .profile import get_or_create_local_user

router = APIRouter(prefix="/applications", tags=["applications"])

_RETRYABLE = {ApplicationStatus.failed, ApplicationStatus.approved}


def _owned(session: Session, application_id: int) -> Application:
    user = get_or_create_local_user(session)
    app = session.get(Application, application_id)
    if app is None or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    return app


```
<!-- /code -->

#### Part 2: Everything the drawer shows

`get_application` returns an `ApplicationView` (stage 4's `schemas.py`) for the drawer:

- The runs are loaded newest first (`order_by(ApplyRun.id.desc())`), so `runs[0]` is the newest. `status_view` gets its status, so the drawer's badge says the same as the history row on the home page.
- `open_run` is true while any run of this application is still going. Two things depend on it. The CV can't change style (`can_restyle(app) and not open_run`), because the agent may be uploading that very file. And `can_retry` stays false, so "Fill the form again" is hidden.
- Each run becomes a `RunSummary`: status, start and end times, cost, number of steps, error, the agent's summary, whether you were at your PC (`attended`), the answers (your corrections included), the fields the agent flagged, and the screenshot URL. The drawer lists them under "What JobBee did".
- `discovered_via=job.discovered_via or job.source` says where the job came from: your pasted link, the watchlist, web search or an alert email.
- Every date goes through `_aware`, as in stage 22.

<!-- code: backend/app/api/applications.py lines=40-96 -->
**`backend/app/api/applications.py`** (lines 40–96 of 190)

```python
@router.get("/{application_id}", response_model=ApplicationView)
def get_application(
    application_id: int, session: Session = Depends(get_session)
) -> ApplicationView:
    app = _owned(session, application_id)
    job = session.get(Job, app.job_id)
    runs = (
        session.execute(
            select(ApplyRun).where(ApplyRun.application_id == app.id).order_by(ApplyRun.id.desc())
        )
        .scalars()
        .all()
    )
    group, label = status_view(app, runs[0].status if runs else None)
    tailoring = app.tailoring or {}
    open_run = any(r.status in OPEN_STATUSES for r in runs)
    return ApplicationView(
        id=app.id,
        status=app.status.value,  # type: ignore[arg-type]
        group=group,  # type: ignore[arg-type]
        label=label,
        stage_reason=app.stage_reason,
        job=job_info(job),
        description=job.description,
        posted_at=_aware(job.posted_at),
        discovered_via=job.discovered_via or job.source,
        screen=app.screen,
        changes=list(tailoring.get("changes") or []),
        removed_claims=list(tailoring.get("removed_claims") or []),
        resume_doc_id=app.resume_doc_id,
        cover_doc_id=app.cover_doc_id,
        style=tailoring.get("style"),
        can_restyle=can_restyle(app) and not open_run,
        runs=[
            RunSummary(
                id=r.id,
                status=r.status,
                created_at=_aware(r.created_at),
                finished_at=_aware(r.finished_at),
                cost_usd=r.cost_usd,
                iterations=r.iterations,
                error=r.error,
                summary=(r.finish or {}).get("summary"),
                attended=r.attended,
                answers=list(r.answers or (r.finish or {}).get("answers") or []),
                checks=list((r.finish or {}).get("fields_needing_review") or []),
                screenshot_url=f"/api/apply-runs/{r.id}/screenshot" if r.screenshot_path else None,
            )
            for r in runs
        ],
        can_retry=not open_run and app.status in _RETRYABLE,
        created_at=_aware(app.created_at),
        updated_at=_aware(app.updated_at),
        submitted_at=_aware(app.submitted_at),
    )


```
<!-- /code -->

#### Part 3: Apply, Skip, Prepare and Retry

**`apply`** calls `job_flow.approve` (stage 12). That moves the application from `awaiting_approval` to `approved` in one atomic step, then queues a run with `run_manager.enqueue` (stage 21) and returns the run's id. The endpoint passes it back in `ActionResponse.run_id`. A second click finds the status already changed, so `approve` raises `FlowError("That one was already handled.")` and the endpoint answers **409**. The same happens if you tapped Apply in Telegram first. `test_home_api.py` checks exactly this.

**`skip`** has an optional body: `payload: SkipRequest | None = None`. The reason, if any, must be one of `not_interested`, `level`, `location`, `salary` or `company`; anything else is a 422. `job_flow.skip` returns a short reply. Skipping with "company" also adds the company to your excluded list, and the reply says "Got it — I won't send Beta jobs again." A skip after an Apply is a 409, like a double Apply.

**`prepare`** answers 202, because tailoring runs in the background. One button covers three cases:

| The application is... | So `prepare`... |
|---|---|
| a card waiting for you, already screened (`awaiting_approval`) | tailors the CV again from your current resume (`start_prepare`) |
| shortlisted or filtered out, already screened | tailors it now, "Prepare anyway" (`start_prepare`) |
| matched but never screened (`screen is None`) | screens and tailors it (`job_flow.start`, with `user_requested=True`) |

It refuses with 409 when the job is already being worked on (it's in `job_flow.stages`), or when its status is anything else, such as `approved` or `submitted` ("This one is already prepared."). `require_ready` runs first, because all three cases call Claude.

**`retry`** is "Fill the form again" in the drawer. Only `failed` and `approved` applications qualify (409 otherwise). `run_manager.retry` moves a failed application back to `approved` and queues a fresh run. A run that's already open gives `RunConflictError`, so 409.

<!-- code: backend/app/api/applications.py lines=97-159 -->
**`backend/app/api/applications.py`** (lines 97–159 of 190)

```python
@router.post("/{application_id}/apply", response_model=ActionResponse)
async def apply(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    try:
        run_id = await job_flow.approve(app.id, app.user_id)
    except FlowError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ActionResponse(
        message="On it. You'll review the filled form before anything is submitted.",
        run_id=run_id,
    )


@router.post("/{application_id}/skip", response_model=ActionResponse)
async def skip(
    application_id: int,
    payload: SkipRequest | None = None,
    session: Session = Depends(get_session),
) -> ActionResponse:
    app = _owned(session, application_id)
    reason = payload.reason if payload else None
    try:
        message = await job_flow.skip(app.id, app.user_id, reason)
    except FlowError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return ActionResponse(message=message)


@router.post(
    "/{application_id}/prepare", response_model=ActionResponse, status_code=status.HTTP_202_ACCEPTED
)
async def prepare(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    require_ready(session, app.user)
    if app.id in job_flow.stages:
        raise HTTPException(status_code=409, detail="Already working on it.")
    if app.status == ApplicationStatus.awaiting_approval and app.screen is not None:
        # A card whose CV should be tailored again, e.g. from a newer resume.
        job_flow.start_prepare(app.id)
        return ActionResponse(message="Tailoring this CV again from your current resume…")
    if app.status not in (ApplicationStatus.matched, ApplicationStatus.filtered_out):
        raise HTTPException(status_code=409, detail="This one is already prepared.")
    if app.screen is None:
        job_flow.start(app.id, user_requested=True)
    else:
        job_flow.start_prepare(app.id)
    return ActionResponse(message="Tailoring your CV…")


@router.post("/{application_id}/retry", response_model=ActionResponse)
async def retry(application_id: int, session: Session = Depends(get_session)) -> ActionResponse:
    app = _owned(session, application_id)
    if app.status not in _RETRYABLE:
        raise HTTPException(status_code=409, detail="There's nothing to retry for this one.")
    try:
        run_id = run_manager.retry(app.id)
    except RunConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except (ValueError, LookupError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return ActionResponse(message="Queued it again.", run_id=run_id)


```
<!-- /code -->

#### Part 4: Change the CV's style

`restyle` handles the style menu on a card and in the drawer:

1. An unknown style id is a 400. `STYLES` comes from stage 10's `render_pdf.py`: modern, classic, minimal, compact and bold.
2. It refuses with 409 while a run is open, while the CV is being tailored, or when `can_restyle` (stage 22) says the CV is no longer allowed to change.
3. If the tailored CV was saved in structured form (`tailoring["cv"]`), `job_flow.restyle` prints the same CV and cover letter again in the new style. There's no Claude call, and the test checks that.
4. A CV made before styles existed has no structured form. Then the endpoint checks readiness, saves the chosen style in `tailoring`, and tailors the CV again in that style, which takes about a minute. `app.tailoring = {**(app.tailoring or {}), "style": ...}` builds a *new* dict on purpose: SQLAlchemy only notices a change to a JSON column when you assign a new value, as stage 21's `answers.py` noted.

<!-- code: backend/app/api/applications.py lines=160-190 -->
**`backend/app/api/applications.py`** (lines 160–190 of 190)

```python
@router.post("/{application_id}/style", response_model=ActionResponse)
async def restyle(
    application_id: int, payload: StyleRequest, session: Session = Depends(get_session)
) -> ActionResponse:
    app = _owned(session, application_id)
    if payload.style not in STYLES:
        raise HTTPException(status_code=400, detail="Unknown CV style.")
    open_run = session.execute(
        select(ApplyRun.id).where(
            ApplyRun.application_id == app.id, ApplyRun.status.in_(OPEN_STATUSES)
        )
    ).first()
    if open_run or app.id in job_flow.stages or not can_restyle(app):
        raise HTTPException(
            status_code=409, detail="This CV can't change style now (it's in use or already sent)."
        )
    name = STYLES[payload.style].name
    if (app.tailoring or {}).get("cv"):
        try:
            await job_flow.restyle(app.id, payload.style)
        except FlowError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return ActionResponse(message=f"Your CV and cover letter now use the {name} style.")
    # Made before styles existed: tailor it again, in the chosen style.
    require_ready(session, app.user)
    app.tailoring = {**(app.tailoring or {}), "style": payload.style}
    session.commit()
    job_flow.start_prepare(app.id)
    return ActionResponse(
        message=f"Rewriting this CV in the {name} style. It takes about a minute."
    )
```
<!-- /code -->

### Step 2: The apply-run controls, `backend/app/api/apply_runs.py`

This file, also in `backend/app/api/`, is the web side of stage 21's run manager. Each endpoint is a thin wrapper: check ownership, call one `run_manager` method, translate its errors, and return the run's fresh state. The same methods are wired to Telegram buttons by `register_chat_actions` (stage 21), so the web and the phone always do the same thing.

| Endpoint | Calls | Button on the page | Success | Errors |
|---|---|---|---|---|
| `GET /api/apply-runs/config` | settings and `PRICES` | none | 200 | |
| `POST /api/applications/{id}/apply-runs` | `enqueue` | none (used in `/docs` and tests) | 202 | 404, 400, 409 |
| `GET /api/apply-runs/{id}?after_id=N` | reads the run and its events | drawer history | 200 | 404 |
| `POST /api/apply-runs/{id}/resume` `{note}` | `answer` | hand-off card: Done | 200 | 409 if the agent isn't waiting |
| `POST /api/apply-runs/{id}/attend` | `attend` | set-aside card: I'm at my PC | 200, the *new* run | 409, 400 |
| `POST /api/apply-runs/{id}/dismiss` | `dismiss` | set-aside card: Skip it | 200 | 409 |
| `POST /api/apply-runs/{id}/revise` `{changes}` | `revise` | review card: change an answer | 202 | 409 |
| `POST /api/apply-runs/{id}/cancel` | `cancel` | applying row: Stop | 200 | 409 |
| `POST /api/apply-runs/{id}/focus` | `focus` | Show (brings the Chrome tab to the front) | 204 | 409 if the tab isn't open |
| `POST /api/apply-runs/{id}/decision` `{submit}` | `decide` | review card: Submit / Don't submit | 200 | 409 if already handled or expired |
| `POST /api/apply-runs/{id}/outcome` `{went_through}` | `resolve_unconfirmed` | unconfirmed card: It went through / It didn't | 200 | 409 |
| `GET /api/apply-runs/{id}/screenshot` | `confined_file` (stage 10) | the review card's picture | 200 | 404 |

#### Part 1: Imports, ownership and fresh answers

- `router = APIRouter(tags=["apply-runs"])` has no prefix, because the paths start two different ways: `/apply-runs/...` and `/applications/{id}/apply-runs`.
- `_EVENT_PAGE = 200` is the page size for events (see "Paging with `after_id`").
- `OutcomeRequest` is a one-field body model kept in this file, because nothing else uses it.
- `_get_run` is the ownership check: it loads the run, then its application, and answers 404 unless the application is yours.
- `_fresh` re-reads the run before answering. The run manager changes runs through its own short database sessions (stage 21's `_transition` and `_update`). The request's session already loaded the run in `_get_run`, and SQLAlchemy keeps loaded objects and reuses them instead of asking the database again. `session.expire_all()` marks them all as stale, so the next read fetches fresh values. Without it, the decision endpoint could answer `awaiting_submit` for a run that is already `submitted`.
- `ApplyRunResponse.model_validate(run)` builds the response from the database object (`from_attributes=True` in the model allows that). The response model is a whitelist. Internal columns, such as the screenshot's file path and the browser tab id, are not in it, so they never leave the server.

<!-- code: backend/app/api/apply_runs.py lines=1-69 -->
**`backend/app/api/apply_runs.py`** (lines 1–69 of 237)

```python
"""Claude apply runs, mirrored in the UI (the same actions are available as chat buttons).

GET  /api/apply-runs/config
POST /api/applications/{id}/apply-runs        queue a run (202)
GET  /api/apply-runs/{run_id}?after_id=N      run + events newer than N
POST /api/apply-runs/{run_id}/resume          "done" after a hand-off (attended runs)
POST /api/apply-runs/{run_id}/attend          set aside → try again with the user at the PC
POST /api/apply-runs/{run_id}/dismiss         set aside → don't apply
POST /api/apply-runs/{run_id}/revise          {changes: [{field, value}]} on a filled form
POST /api/apply-runs/{run_id}/cancel
POST /api/apply-runs/{run_id}/focus           bring the run's browser tab to the front
POST /api/apply-runs/{run_id}/decision        {submit: bool} for a filled form
POST /api/apply-runs/{run_id}/outcome         {went_through: bool} when submit wasn't confirmed
GET  /api/apply-runs/{run_id}/screenshot      the filled form, as reviewed
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..apply.agent import EFFORTS
from ..apply.costs import PRICES
from ..apply.runs import RunConflictError, RunStateError, run_manager
from ..config import get_settings
from ..db import get_session
from ..models import Application, ApplyEvent, ApplyRun
from ..schemas import (
    ApplyAgentConfigResponse,
    ApplyEventResponse,
    ApplyRunDecisionRequest,
    ApplyRunDetailResponse,
    ApplyRunResponse,
    ApplyRunResumeRequest,
    ApplyRunReviseRequest,
    StartApplyRunRequest,
)
from ..services.secrets import claude_api_key
from .documents import confined_file
from .profile import get_or_create_local_user

router = APIRouter(tags=["apply-runs"])

_EVENT_PAGE = 200


class OutcomeRequest(BaseModel):
    went_through: bool


def _get_run(session: Session, run_id: int) -> ApplyRun:
    user = get_or_create_local_user(session)
    run = session.get(ApplyRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found.")
    app = session.get(Application, run.application_id)
    if app is None or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Run not found.")
    return run


def _fresh(session: Session, run_id: int) -> ApplyRunResponse:
    session.expire_all()
    return ApplyRunResponse.model_validate(_get_run(session, run_id))


```
<!-- /code -->

#### Part 2: Settings, starting a run, and reading one

- **`apply_agent_config`** reports which models you can pick (every model with a price in stage 6's `PRICES`), the effort levels (`EFFORTS` from stage 18), the defaults and the cost cap per application. `api_key_configured` is a yes/no, never the key.
- **`start_apply_run`** queues a run directly, optionally with another model or effort. It skips `job_flow.approve`, so there's no chat message and no card buttons are cleared. The web app's Apply button uses `/api/applications/{id}/apply` instead. This endpoint is handy in `/docs`, and the run tests use it. `enqueue`'s three errors map to three codes: an open run is 409, a missing application 404, an unknown model or an unapproved application 400.
- **`get_apply_run`** returns the run plus up to 200 events newer than `after_id`. `Query(default=0, ge=0)` makes `after_id` optional and refuses negative numbers.

<!-- code: backend/app/api/apply_runs.py lines=70-127 -->
**`backend/app/api/apply_runs.py`** (lines 70–127 of 237)

```python
@router.get("/apply-runs/config", response_model=ApplyAgentConfigResponse)
def apply_agent_config() -> ApplyAgentConfigResponse:
    settings = get_settings()
    return ApplyAgentConfigResponse(
        api_key_configured=bool(claude_api_key()),
        models=sorted(PRICES),
        default_model=settings.apply_model,
        efforts=list(EFFORTS),
        default_effort=settings.apply_effort,
        max_cost_usd=settings.apply_max_cost_usd,
    )


@router.post(
    "/applications/{application_id}/apply-runs",
    response_model=ApplyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_apply_run(
    application_id: int,
    payload: StartApplyRunRequest | None = None,
    session: Session = Depends(get_session),
) -> ApplyRunResponse:
    user = get_or_create_local_user(session)
    app = session.get(Application, application_id)
    if app is None or app.user_id != user.id:
        raise HTTPException(status_code=404, detail="Application not found.")
    req = payload or StartApplyRunRequest()
    try:
        run_id = run_manager.enqueue(application_id, model=req.model, effort=req.effort)
    except RunConflictError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _fresh(session, run_id)


@router.get("/apply-runs/{run_id}", response_model=ApplyRunDetailResponse)
def get_apply_run(
    run_id: int,
    after_id: int = Query(default=0, ge=0),
    session: Session = Depends(get_session),
) -> ApplyRunDetailResponse:
    run = _get_run(session, run_id)
    events = session.execute(
        select(ApplyEvent)
        .where(ApplyEvent.run_id == run.id, ApplyEvent.id > after_id)
        .order_by(ApplyEvent.id)
        .limit(_EVENT_PAGE)
    ).scalars()
    return ApplyRunDetailResponse(
        run=ApplyRunResponse.model_validate(run),
        events=[ApplyEventResponse.model_validate(e) for e in events],
    )


```
<!-- /code -->

#### Part 3: Hand-offs, set-asides, revisions, Stop and Show

- **`resume_apply_run`** is "Done" on a hand-off card. It first checks that the run is waiting for a person (`pending["type"] == "human_action"`, set by stage 21's `request_human`). Then `run_manager.answer` hands your note to the waiting agent. An empty note means "done". A note like "I couldn't do it" goes to the agent as your reply.
- **`attend_apply_run`** is "I'm at my PC". `run_manager.attend` queues a *new*, attended run and returns its id, so the endpoint answers with the new run, not the old one. A second press on the same card gets 409, because the old run is no longer the newest.
- **`dismiss_apply_run`** is "Skip it" on a set-aside card: the run becomes `discarded` and the application `rejected_by_user`.
- **`revise_apply_run`** sends your corrections for a parked form. `ApplyRunReviseRequest` (stage 4) allows 1 to 20 changes, each with a field name of 1 to 300 characters and a value of up to 2000. The list comprehension trims spaces and drops changes whose field name is blank. `run_manager.revise` moves the run to `revising` and lines up the work for the worker, so the answer is 202.
- **`cancel_apply_run`** is Stop. Stage 21's `cancel` handles three cases: a queued run is cancelled in place, a filling run's task is cancelled (its tab stays open, in case you want to take over), and a parked form counts as "Don't submit".
- **`focus_apply_run`** is Show: it brings the run's Chrome tab to the front. It answers **204 No Content**, which means "done, nothing to send back", so the function returns `None`.

<!-- code: backend/app/api/apply_runs.py lines=128-209 -->
**`backend/app/api/apply_runs.py`** (lines 128–209 of 237)

```python
@router.post("/apply-runs/{run_id}/resume", response_model=ApplyRunResponse)
async def resume_apply_run(
    run_id: int, payload: ApplyRunResumeRequest, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    run = _get_run(session, run_id)
    if (run.pending or {}).get("type") != "human_action":
        raise HTTPException(status_code=409, detail="The agent isn't waiting for you.")
    try:
        run_manager.answer(run_id, payload.note)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.post("/apply-runs/{run_id}/attend", response_model=ApplyRunResponse)
async def attend_apply_run(
    run_id: int, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    try:
        new_run_id = run_manager.attend(run_id)
    except (RunStateError, RunConflictError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _fresh(session, new_run_id)


@router.post("/apply-runs/{run_id}/dismiss", response_model=ApplyRunResponse)
async def dismiss_apply_run(
    run_id: int, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    try:
        run_manager.dismiss(run_id)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.post(
    "/apply-runs/{run_id}/revise",
    response_model=ApplyRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revise_apply_run(
    run_id: int, payload: ApplyRunReviseRequest, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    changes = [
        {"field": c.field.strip(), "value": c.value.strip()}
        for c in payload.changes
        if c.field.strip()
    ]
    try:
        await run_manager.revise(run_id, changes)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.post("/apply-runs/{run_id}/cancel", response_model=ApplyRunResponse)
async def cancel_apply_run(
    run_id: int, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    try:
        await run_manager.cancel(run_id)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.post("/apply-runs/{run_id}/focus", status_code=status.HTTP_204_NO_CONTENT)
async def focus_apply_run(run_id: int, session: Session = Depends(get_session)) -> None:
    _get_run(session, run_id)
    try:
        await run_manager.focus(run_id)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


```
<!-- /code -->

#### Part 4: Your decision, the outcome, and the screenshot

- **`decide_apply_run`** is the review card's Submit or Don't submit. `run_manager.decide` (stage 21) first moves the run from `awaiting_submit` to `submitting` or `discarded` in one atomic step. On Submit, the parked driver then clicks the page's one final-submit button once (`click_final_submit`, stage 17) and looks for a confirmation. A second decision, or one after the form expired, finds the status already changed: `RunStateError`, so 409. This is the code-level guarantee that one click can never submit twice.
- **`confirm_apply_outcome`** answers the "Did it go through?" card. `resolve_unconfirmed` marks the run `submitted` or `failed`, and the application with it.
- **`apply_run_screenshot`** serves the picture of the filled form. Stage 21 saved it under `backend/data/apply_runs/<run id>/review.png`, which is inside JobBee's data folder, so `confined_file` lets it through. A run without a screenshot gets 404.

<!-- code: backend/app/api/apply_runs.py lines=210-237 -->
**`backend/app/api/apply_runs.py`** (lines 210–237 of 237)

```python
@router.post("/apply-runs/{run_id}/decision", response_model=ApplyRunResponse)
async def decide_apply_run(
    run_id: int, payload: ApplyRunDecisionRequest, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    try:
        await run_manager.decide(run_id, submit=payload.submit)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.post("/apply-runs/{run_id}/outcome", response_model=ApplyRunResponse)
async def confirm_apply_outcome(
    run_id: int, payload: OutcomeRequest, session: Session = Depends(get_session)
) -> ApplyRunResponse:
    _get_run(session, run_id)
    try:
        await run_manager.resolve_unconfirmed(run_id, went_through=payload.went_through)
    except RunStateError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return _fresh(session, run_id)


@router.get("/apply-runs/{run_id}/screenshot")
def apply_run_screenshot(run_id: int, session: Session = Depends(get_session)) -> FileResponse:
    run = _get_run(session, run_id)
    return FileResponse(confined_file(run.screenshot_path), media_type="image/png")
```
<!-- /code -->

### Step 3: First-run setup and the Claude key, `backend/app/api/setup.py`

This file, in `backend/app/api/`, serves the welcome flow (stage 25) and the Claude section of the settings page (stage 28). All its paths start with `/api/setup`.

#### Part 1: Imports, the router and the workspace hint

The imports bring in the Anthropic SDK's client and error classes, the secret-store helpers from stage 2, `set_flag` from stage 5, `reset_llm_provider` and `spend_summary` from stage 6, and `save_env` through `settings_api` (stage 6's `api/settings.py`).

`WORKSPACE_HINT` is a longer explanation for one special case. Some Anthropic keys work for a whole organization, and then every request must also name a *workspace*. JobBee keeps the workspace id as a flag in the database (it isn't a secret), and stage 2's `claude_headers` adds it to every Claude request as the `anthropic-workspace-id` header.

<!-- code: backend/app/api/setup.py lines=1-49 -->
**`backend/app/api/setup.py`** (lines 1–49 of 185)

```python
"""First-run setup and the Claude connection.

GET    /api/setup                what's done: Claude key, resume, onboarding, Telegram, inbox
POST   /api/setup/finish         end the welcome flow and run the first job search
GET    /api/setup/claude         key status, models and spend
PUT    /api/setup/claude-key     check a key (and workspace) with Anthropic; key → OS keyring
DELETE /api/setup/claude-key
PUT    /api/setup/budget         daily / monthly spend limits (saved to backend/.env)
"""

from __future__ import annotations

from datetime import datetime, timezone

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, AuthenticationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..llm.factory import reset_llm_provider
from ..llm.usage import spend_summary
from ..pipeline.discovery import discovery
from ..schemas import ActionResponse, BudgetUpdate, ClaudeKeyRequest, ClaudeStatus, SetupStatus
from ..pipeline.state import set_flag
from ..services.secrets import (
    ANTHROPIC_API_KEY,
    WORKSPACE_ID_KEY,
    SecretStoreError,
    claude_api_key,
    claude_headers,
    claude_workspace_id,
    get_secret_store,
)
from . import settings as settings_api
from .agent import require_ready
from .home import setup_status
from .profile import get_or_create_local_user

router = APIRouter(prefix="/setup", tags=["setup"])


WORKSPACE_HINT = (
    "This key works for your whole Anthropic organization, so JobBee also needs to know which "
    "workspace to use. Paste the workspace ID (Console → Settings → Workspaces, it starts with "
    "wrkspc_), or create a key inside a workspace instead."
)


```
<!-- /code -->

#### Part 2: Testing a key with Anthropic

`validate_claude_key` asks Anthropic whether a key works, before JobBee saves it:

- It builds an `AsyncAnthropic` client with the key being tested, not the saved one. The address is fixed to `https://api.anthropic.com`, and the timeout is 20 seconds.
- **`count_tokens`** asks "how many tokens is this message?". It costs nothing, but Anthropic checks the key, and the workspace, exactly as it would for a real message. A free call that proves the key works is the ideal test.
- Each kind of failure gets a clear message. A rejected key is 400, because your input was wrong. No connection is **502 Bad Gateway**: the server JobBee depends on couldn't be reached. Any other error repeats Anthropic's own message, which `_api_message` digs out of the error's body. If that message mentions a workspace and you didn't give one, you get `WORKSPACE_HINT` instead.
- `finally: await client.close()` releases the client's network connection, whether the test passed or not.

<!-- code: backend/app/api/setup.py lines=50-89 -->
**`backend/app/api/setup.py`** (lines 50–89 of 185)

```python
def _api_message(error: APIStatusError) -> str:
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    return str(detail.get("message") or error.message)


async def validate_claude_key(api_key: str, workspace_id: str = "") -> None:
    """Raise HTTPException unless Anthropic accepts the key for real requests.

    Counting tokens is free and goes through the same checks as a message, so it also
    catches organization-wide keys that need a workspace.
    """

    client = AsyncAnthropic(
        api_key=api_key,
        base_url="https://api.anthropic.com",
        timeout=20.0,
        default_headers=claude_headers(workspace_id) or None,
    )
    try:
        await client.messages.count_tokens(
            model=get_settings().match_model,
            messages=[{"role": "user", "content": "ping"}],
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=400, detail="Anthropic didn't accept that key.") from e
    except APIConnectionError as e:
        raise HTTPException(
            status_code=502, detail="Couldn't reach Anthropic. Check your connection."
        ) from e
    except APIStatusError as e:
        message = _api_message(e)
        if "workspace" in message.lower():
            detail = WORKSPACE_HINT if not workspace_id else f"Anthropic said: {message}"
            raise HTTPException(status_code=400, detail=detail) from e
        raise HTTPException(status_code=400, detail=f"Anthropic said: {message}") from e
    finally:
        await client.close()


```
<!-- /code -->

#### Part 3: Key status, setup status and finishing setup

- **`_stored_key`** reads the key from the keyring. If the keyring can't be read at all (`SecretStoreError`), it treats that as "no key", so the status page still loads.
- **`_claude_status`** builds the Claude section's data. `source` is worked out in one line: `"app"` if a key is saved in the keyring, otherwise `"env"` if `backend/.env` has one, otherwise `"none"`. Then come the four models from `config.py` (Haiku for screening, Sonnet for tailoring and job search, Opus for applying), the workspace, and today's and this month's spend against the limits from stage 6.
- **`read_setup`** (`GET /api/setup`) returns the same `setup_status` that `GET /api/home` includes (stage 22). The welcome flow needs it before there's anything else to show.
- **`finish_setup`** is the welcome flow's last button. It checks readiness, and also that you named at least one target role. It stamps `onboarded_at` once; a second call keeps the first date. From then on the scheduler includes you in the daily search (stage 19). Finally it starts the first search right away. It's `async def` because `discovery.start` needs the event loop (stage 22).

<!-- code: backend/app/api/setup.py lines=90-139 -->
**`backend/app/api/setup.py`** (lines 90–139 of 185)

```python
def _stored_key() -> str | None:
    try:
        value = get_secret_store().get(ANTHROPIC_API_KEY)
    except SecretStoreError:
        return None
    return value.strip() if value and value.strip() else None


def _claude_status() -> ClaudeStatus:
    settings = get_settings()
    stored = _stored_key()
    source = "app" if stored else "env" if settings.anthropic_api_key.strip() else "none"
    spend = spend_summary()
    return ClaudeStatus(
        connected=source != "none",
        source=source,  # type: ignore[arg-type]
        models={
            "screening": settings.match_model,
            "tailoring": settings.tailor_model,
            "job search": settings.discover_model,
            "applying": settings.apply_model,
        },
        workspace_id=claude_workspace_id(),
        spend_today_usd=round(spend["today_usd"], 2),
        spend_month_usd=round(spend["month_usd"], 2),
        daily_budget_usd=spend["daily_budget_usd"],
        monthly_budget_usd=spend["monthly_budget_usd"],
    )


@router.get("", response_model=SetupStatus)
def read_setup(session: Session = Depends(get_session)) -> SetupStatus:
    return setup_status(session, get_or_create_local_user(session))


@router.post("/finish", response_model=ActionResponse)
async def finish_setup(session: Session = Depends(get_session)) -> ActionResponse:
    user = get_or_create_local_user(session)
    require_ready(session, user)
    if not user.target_roles:
        raise HTTPException(status_code=400, detail="Tell JobBee which roles you want first.")
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
        session.commit()
    started = discovery.start(user.id, "manual")
    return ActionResponse(
        message="Looking for your first jobs…" if started else "A search is already running."
    )


```
<!-- /code -->

#### Part 4: Save, forget, and change the limits

**`save_claude_key`** handles three requests with one body:

1. **A new key.** Test it, with the saved workspace if there is one, then save it.
2. **Only a workspace id**, for a key you already have. `key` falls back to `claude_api_key()`, and the pair is tested together.
3. **Both.** The same steps.

The order matters: **test first, save second**. A key that doesn't work is never stored, so the app can't end up "connected" with a broken key. If the keyring refuses to save, the answer is 500 with "Couldn't save the key to Windows Credential Manager." The message names Windows because JobBee was written for it. Then the workspace is saved as a flag, and `reset_llm_provider()` throws away stage 6's cached Claude client, so the next call is made with the new key. The answer is `_claude_status()`, which has no key in it. `ClaudeKeyRequest` (stage 4) has already refused a key shorter than 20 characters or a workspace id with odd characters (422).

**`forget_claude_key`** deletes the key from the keyring, clears the workspace, and resets the Claude client. If `backend/.env` also holds a key, the status then says `"source": "env"` and you're still connected.

**`update_budget`** changes the daily and monthly spending limits. Only the limits you send are changed. `f"{value:g}"` writes a number without needless zeros, so `5.0` becomes `5`. `settings_api.save_env` (stage 6) writes them into `backend/.env` and reloads the settings, so the new limits apply at once. `BudgetUpdate` allows 0 to 1000 a day and 0 to 10000 a month; 0 means "no limit".

<!-- code: backend/app/api/setup.py lines=140-185 -->
**`backend/app/api/setup.py`** (lines 140–185 of 185)

```python
@router.get("/claude", response_model=ClaudeStatus)
def claude_status() -> ClaudeStatus:
    return _claude_status()


@router.put("/claude-key", response_model=ClaudeStatus)
async def save_claude_key(payload: ClaudeKeyRequest) -> ClaudeStatus:
    """Save a new key and/or the workspace for an organization-wide key (both are checked)."""

    key = (payload.api_key or "").strip() or claude_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="Paste your Anthropic API key.")
    workspace = (
        claude_workspace_id() if payload.workspace_id is None else payload.workspace_id.strip()
    )
    await validate_claude_key(key, workspace)
    if payload.api_key:
        try:
            get_secret_store().set(ANTHROPIC_API_KEY, key)
        except SecretStoreError as e:
            raise HTTPException(
                status_code=500, detail="Couldn't save the key to Windows Credential Manager."
            ) from e
    set_flag(WORKSPACE_ID_KEY, workspace)
    reset_llm_provider()
    return _claude_status()


@router.delete("/claude-key", response_model=ClaudeStatus)
def forget_claude_key() -> ClaudeStatus:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    set_flag(WORKSPACE_ID_KEY, "")
    reset_llm_provider()
    return _claude_status()


@router.put("/budget", response_model=ClaudeStatus)
def update_budget(payload: BudgetUpdate) -> ClaudeStatus:
    updates = {}
    if payload.daily_budget_usd is not None:
        updates["DAILY_BUDGET_USD"] = f"{payload.daily_budget_usd:g}"
    if payload.monthly_budget_usd is not None:
        updates["MONTHLY_BUDGET_USD"] = f"{payload.monthly_budget_usd:g}"
    if updates:
        settings_api.save_env(updates)
    return _claude_status()
```
<!-- /code -->

### Step 4: Site accounts, `backend/app/api/site_accounts.py`

Stage 20 built `apply/accounts.py`: the job-site accounts the apply agent creates for you, with passwords that JobBee generates and keeps in the keyring. This small file, in `backend/app/api/`, lets the settings page (stage 28) list those accounts, show a password, and forget one. Type it even if you never let the agent create accounts: `main.py` imports it, and the list stays empty. It has three endpoints:

- **`list_site_accounts`** returns host, login, status and dates, most recently used first. No password.
- **`reveal_site_account`** returns one password, read from the keyring with stage 20's `account_password`. If the keyring entry is gone, it's a 404 with "The password isn't saved on this PC any more."
- **`delete_site_account`** calls stage 20's `forget`, which deletes the keyring entry and the database row. The account on the job site itself stays. JobBee can't delete it for you.

Reveal is the one place where JobBee sends a secret back. Three things make that acceptable:

1. **They're your own logins.** JobBee made the account in your name. You may need the password to sign in to the site and check your application.
2. **It takes an explicit click.** It's a `POST`, so it's a deliberate action: browsers don't prefetch or cache `POST` requests. The list never includes passwords, so they don't travel on every page load.
3. **The API only listens on localhost.** Only programs on your own PC can call it (see "Who can reach the API: localhost"). On top of that, the CORS rule from stage 1 stops a web page from another site, open in your browser, from reading the answer.

The Claude key is different: you already have it, nothing in the app needs it back, and leaking it costs money. So it stays write-only.

<!-- code: backend/app/api/site_accounts.py -->
**`backend/app/api/site_accounts.py`** (whole file, 70 lines)

```python
"""Job-site accounts the apply agent made for the user (Settings → Applying for you).

GET    /api/site-accounts
POST   /api/site-accounts/{id}/reveal     the password, for one explicit click
DELETE /api/site-accounts/{id}            forget it (the account on the site stays)

Reveal is the one place JobBee returns a secret: these are the user's own logins,
which they may need to check an application on the site themselves. The API
only listens on localhost.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..apply.accounts import account_password, forget
from ..db import get_session
from ..models import SiteAccount
from ..schemas import SiteAccountSecret, SiteAccountView
from .home import _aware
from .profile import get_or_create_local_user

router = APIRouter(prefix="/site-accounts", tags=["site-accounts"])


def _account(session: Session, account_id: int) -> SiteAccount:
    user = get_or_create_local_user(session)
    account = session.get(SiteAccount, account_id)
    if account is None or account.user_id != user.id:
        raise HTTPException(status_code=404, detail="Account not found.")
    return account


@router.get("", response_model=list[SiteAccountView])
def list_site_accounts(session: Session = Depends(get_session)) -> list[SiteAccountView]:
    user = get_or_create_local_user(session)
    rows = session.execute(
        select(SiteAccount)
        .where(SiteAccount.user_id == user.id)
        .order_by(SiteAccount.last_used_at.desc(), SiteAccount.id.desc())
    ).scalars()
    return [
        SiteAccountView(
            id=a.id,
            host=a.host,
            login=a.login,
            status=a.status,
            created_at=_aware(a.created_at),
            last_used_at=_aware(a.last_used_at),
        )
        for a in rows
    ]


@router.post("/{account_id}/reveal", response_model=SiteAccountSecret)
def reveal_site_account(
    account_id: int, session: Session = Depends(get_session)
) -> SiteAccountSecret:
    account = _account(session, account_id)
    password = account_password(account.user_id, account.host)
    if not password:
        raise HTTPException(status_code=404, detail="The password isn't saved on this PC any more.")
    return SiteAccountSecret(password=password)


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site_account(account_id: int, session: Session = Depends(get_session)) -> None:
    forget(session, _account(session, account_id))
```
<!-- /code -->

### Step 5: The final `backend/app/main.py`

This replaces the temporary `main.py` you have grown since stage 1. It's the last backend file.

#### Part 1: The event loop setting and the imports

- The docstring now says how to run the dev server. `--reload` restarts the server whenever you save a file, which is handy while you edit code. Don't use it while JobBee is filling or holding a form: a restart drops the form (stage 21).
- The Windows event loop setting is the same as in stage 1: Playwright starts Chrome as a separate process, and the default Windows loop can't do that.
- The router imports look different from stage 22's: one module per `from .api import ( ... )` block, in alphabetical order. That's the layout ruff, the linter set up in `pyproject.toml` (stage 1), produces when it sorts imports. It imports the same modules, plus four new ones: `applications`, `apply_runs`, `setup` and `site_accounts`.
- The last imports are the long-lived services the lifespan starts and stops.

<!-- code: backend/app/main.py lines=1-71 -->
**`backend/app/main.py`** (lines 1–71 of 136)

```python
"""FastAPI entry point.

`uvicorn app.main:app --reload` runs the dev server.
"""

from __future__ import annotations

import asyncio
import sys

# Playwright spawns a browser subprocess; uvicorn's default Windows loop cannot do that.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api import (
    agent as agent_router,
)
from .api import (
    applications as applications_router,
)
from .api import (
    apply_runs as apply_runs_router,
)
from .api import (
    documents as documents_router,
)
from .api import (
    email as email_router,
)
from .api import (
    health,
    llm,
)
from .api import (
    home as home_router,
)
from .api import (
    messaging as messaging_router,
)
from .api import (
    preferences as preferences_router,
)
from .api import (
    profile as profile_router,
)
from .api import (
    settings as settings_router,
)
from .api import (
    setup as setup_router,
)
from .api import (
    site_accounts as site_accounts_router,
)
from .api import (
    watchlist as watchlist_router,
)
from .apply.runs import run_manager
from .config import get_settings
from .db import init_db
from .messaging.service import messaging
from .pipeline.discovery import discovery
from .pipeline.flow import job_flow
from .pipeline.scheduler import scheduler

```
<!-- /code -->

#### Part 2: The lifespan and `create_app`

**The lifespan** is identical to stage 22's. Stage 3 introduced it: the code before `yield` runs once when the server starts, the code after `yield` once when it stops. Here it is in full, in order:

| # | Line | What it does | Built in |
|---|---|---|---|
| 1 | `init_db()` | creates or upgrades the database with the Alembic migrations | stage 3 |
| 2 | `job_flow.register()` | connects the chat buttons Apply, Skip and the skip reasons, and pasted links, to the job flow | stage 12 |
| 3 | `run_manager.register_chat_actions(messaging)` | connects the run buttons (Submit, Don't submit, I'm at my PC, ...) to the run manager | stage 21 |
| 4 | `await run_manager.startup()` | marks runs that a restart interrupted as failed or expired, then wakes the worker for queued runs | stage 21 |
| 5 | `await messaging.start()` | starts reading Telegram messages, if a bot is set up | stage 11 |
| 6 | `scheduler.start()` | starts the daily search timer, except in tests | stage 19 |
| | `yield` | the server handles requests | |
| 7 | `await scheduler.stop()` | stops the timer, so no new search starts | stage 19 |
| 8 | `await discovery.stop()` | cancels a search that's running | stage 19 |
| 9 | `await messaging.stop()` | stops Telegram and cancels background tasks, such as tailoring | stage 11 |
| 10 | `await run_manager.shutdown()` | cancels the worker and any fill, lets go of parked forms, quits Chrome | stage 21 |

Why this order:

- **Start-up goes from the bottom layer up.** The database comes first, because everything else reads it. The button handlers are registered before Telegram starts, so a tap never arrives with no handler. Old runs are cleaned up before anything can queue a new one. The scheduler starts last, and never in tests (`APP_ENV=test`), so a test never kicks off a real search on a timer.
- **Shutdown first stops whatever creates new work**: the timer, then a running search. Then messaging stops, so no chat tap can reach the run manager while it closes Chrome. The run manager goes last.

**`create_app`** is unchanged from stage 22 except for four new `include_router` lines. Every router gets `prefix="/api"`:

| Router | Its own prefix | Example URL | Built in |
|---|---|---|---|
| `health` | none | `/api/health` | stage 4 |
| `settings` | `/settings` | `/api/settings` | stage 6 |
| `llm` | `/llm` | `/api/llm/test` | stage 6 |
| `profile` | `/profile` | `/api/profile/import` | stage 7 |
| `documents` | `/documents` | `/api/documents/{id}/file` | stage 10 |
| `applications` (new) | `/applications` | `/api/applications/{id}/apply` | stage 23 |
| `apply_runs` (new) | none | `/api/apply-runs/{id}/decision` | stage 23 |
| `email` | `/email` | `/api/email/status` | stage 13 |
| `messaging` | `/messaging` | `/api/messaging/status` | stage 11 |
| `home` | none | `/api/home` | stage 22 |
| `agent` | none | `/api/agent/search`, `/api/jobs/link` | stage 22 |
| `setup` (new) | `/setup` | `/api/setup/claude-key` | stage 23 |
| `preferences` | `/preferences` | `/api/preferences` | stage 10 |
| `watchlist` | `/watchlist` | `/api/watchlist` | stage 14 |
| `site_accounts` (new) | `/site-accounts` | `/api/site-accounts/{id}/reveal` | stage 23 |

No two routers share a path, so the order doesn't change which code answers a URL. It only sets the order of the groups in `/docs`.

**Compared with the last temporary version (stage 22):** the lifespan, the CORS setup and the `/` route are identical. What changed is the docstring, the import layout, and the four new routers: `applications` and `apply_runs` after `documents`, `setup` after `agent`, and `site_accounts` at the end. From here on, `main.py` doesn't change again.

<!-- code: backend/app/main.py lines=72-136 -->
**`backend/app/main.py`** (lines 72–136 of 136)

```python

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    job_flow.register()
    run_manager.register_chat_actions(messaging)
    await run_manager.startup()
    await messaging.start()
    if get_settings().app_env != "test":
        scheduler.start()
    yield
    await scheduler.stop()
    await discovery.stop()
    await messaging.stop()
    await run_manager.shutdown()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="JobBee API",
        version=__version__,
        lifespan=lifespan,
    )

    # In dev the Vite proxy handles same-origin, but allow direct browser calls too.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"]
        if settings.is_dev
        else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router, prefix="/api")
    app.include_router(settings_router.router, prefix="/api")
    app.include_router(llm.router, prefix="/api")
    app.include_router(profile_router.router, prefix="/api")
    app.include_router(documents_router.router, prefix="/api")
    app.include_router(applications_router.router, prefix="/api")
    app.include_router(apply_runs_router.router, prefix="/api")
    app.include_router(email_router.router, prefix="/api")
    app.include_router(messaging_router.router, prefix="/api")
    app.include_router(home_router.router, prefix="/api")
    app.include_router(agent_router.router, prefix="/api")
    app.include_router(setup_router.router, prefix="/api")
    app.include_router(preferences_router.router, prefix="/api")
    app.include_router(watchlist_router.router, prefix="/api")
    app.include_router(site_accounts_router.router, prefix="/api")

    @app.get("/", include_in_schema=False)
    def root() -> dict:
        return {
            "name": "JobBee API",
            "version": __version__,
            "docs": "/docs",
            "health": "/api/health",
        }

    return app


app = create_app()
```
<!-- /code -->

## Check it works

This stage's check is the whole backend test suite. First, a closer look at the five big test files that arrive now. Together they drive the code from stages 8 to 23. To run only these five, type this in the `backend` folder:

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests/test_pipeline.py tests/test_discovery.py tests/test_home_api.py tests/test_apply_runs.py tests/test_site_accounts.py
```

On Mac or Linux, use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe`. You should see:

```text
63 passed
```

(pytest adds the time it took, such as `in 9.40s`.)

### How the tests fake the outside world

None of these tests call Claude, open Chrome, use the network or message Telegram. Each replaces only the slow or costly edge, and runs the real code in between:

| Technique | What it does | Where |
|---|---|---|
| `monkeypatch.setattr(module, "name", fake)` | swaps a name inside a module for the length of one test, then puts it back | everywhere |
| `ScriptedProvider` | a fake Claude with the same `chat()` method; it reads the system prompt and returns canned JSON | `test_pipeline.py`, reused by two others |
| fake fetchers and `fake_pdf` | return a `JobDetails` instead of downloading a page; write a few bytes instead of printing with Chrome | the first three files |
| `FakeChannel` | records chat messages instead of sending them to Telegram | `test_pipeline.py` |
| `FakeSession`, `FakeDriver`, `fake_run_agent` | a pretend browser and a scripted "Claude" for apply runs | `test_apply_runs.py` |
| `patch.object(..., side_effect=...)` | answers HTTP calls from a function, like the `httpx.MockTransport` tests in stages 7, 11 and 14 | `test_site_accounts.py` |

Three more things help:

- **`monkeypatch` only works because the code looks names up when it runs.** For example, `flow.py` calls `get_llm_provider()` by name each time it needs Claude. When a test replaces `flow_module.get_llm_provider`, the next call finds the fake.
- **`with TestClient(app) as client:` runs the lifespan**, so the run manager and messaging start as they would in real use. Background tasks run on the test client's event loop. Helpers such as `_wait_for` and `_wait` poll an endpoint every 20 to 30 milliseconds until the expected state shows up, the same way the web page polls.
- **All tests share one temporary database** for the whole run (stage 4's `conftest.py`). That's why many tests put `time.time_ns()` into URLs and company names, so their rows never clash, and why some delete their leftovers.

### The job flow: `tests/test_pipeline.py`

It proves the full chain from stage 12: a link is read, filtered, screened, tailored and fact-checked, an approval card is sent, and Apply or Skip works exactly once. It goes through the chat path (stage 11), with the run manager's `enqueue` replaced by a function that only records the id.

`ScriptedProvider` returns a fixed verdict (keep, 82/100), the `TAILORED` CV and, for the fact-check, `{"unsupported": ["experience.0.b1", "skill.2"]}`. It records every call in `calls`, so tests can check which Claude calls happened and in what order.

Three stories:

1. **`test_tailor_removes_unverified_claims`.** The fact-check flags the second experience bullet and the third skill. The tailored CV loses "Led a team of 40 engineers" and "Kubernetes", both appear in `removed_claims`, and the plain-text CV no longer mentions them.
2. **`test_link_to_card_then_apply`.** A Lever link is pasted in chat. The card arrives as a PDF named `Lin_Ho_Resume.pdf`, with "Match 82/100", the salary and "Removed 2 claim(s)". Claude was called three times: screen, tailor, fact-check. Tapping Apply queues exactly one run and leaves the application `approved`. Tapping Skip on the same card afterwards changes nothing.
3. **`test_ingest_dedupes_by_canonical_url`.** The same posting with `?utm_source=linkedin` added is recognised as a duplicate, with the same application id. A `localhost` link is refused with `IngestError`.

<!-- code: backend/tests/test_pipeline.py collapsed -->
<details>
<summary><code>backend/tests/test_pipeline.py</code> (whole file, 328 lines)</summary>

```python
"""Link → ingest → filters → screen → tailor (+fact-check) → approval card → Apply/Skip."""

from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.llm.base import ChatResponse, LLMError
from app.main import app
from app.messaging.service import MessagingService
from app.models import Application, ApplicationStatus, Job, MessagingLink, User
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline.flow import JobFlow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.prefs import Preferences, load_preferences
from app.pipeline.screen import hard_filter_reason, screen_job
from app.pipeline.tailor import cv_to_text, tailor_cv

TAILORED = {
    "resume": {
        "name": "Lin Ho",
        "headline": "Backend Engineer",
        "contact": ["lin@example.com"],
        "summary": "Backend engineer focused on Python APIs.",
        "skills": ["Python", "FastAPI", "Kubernetes"],
        "experience": [
            {
                "title": "Engineer",
                "organization": "Acme",
                "start": "2021",
                "end": "Present",
                "bullets": ["Built FastAPI services", "Led a team of 40 engineers"],
            }
        ],
        "education": [],
    },
    "cover_letter": "Dear Beta team,\nI'd love to help.\nLin",
    "changes": ["Led with API work", "Moved FastAPI to the top of skills"],
}


class ScriptedProvider:
    name = "scripted"
    default_model = "scripted"

    def __init__(self, screen: dict | None = None, unsupported: list[str] | None = None):
        self.screen = screen or {
            "decision": "keep",
            "score": 82,
            "summary": "Strong Python API overlap.",
            "reasons": ["Python", "APIs"],
            "gaps": ["No Go experience"],
        }
        self.unsupported = (
            unsupported if unsupported is not None else ["experience.0.b1", "skill.2"]
        )
        self.calls: list[str] = []
        self.prompts: list[str] = []

    async def chat(self, messages, **kwargs):
        system = messages[0].content
        self.prompts.append(messages[-1].content)
        if "screen job postings" in system:
            self.calls.append("screen")
            body = self.screen
        elif "tailor a candidate" in system:
            self.calls.append("tailor")
            body = TAILORED
        else:
            self.calls.append("fact_check")
            body = {"unsupported": self.unsupported}
        return ChatResponse(content=json.dumps(body), model="m", provider="scripted", raw={})


def _user() -> User:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.work_modes = ["remote", "hybrid"]
        user.target_locations = ["Toronto"]
        user.location = "Toronto, ON"
        user.preferences = {}
        session.commit()
        session.refresh(user)
        session.expunge(user)
        return user


def _job(**overrides) -> Job:
    data = dict(
        source="test",
        external_id=str(time.time_ns()),
        url="https://x.test/job",
        title="Backend Engineer",
        company="Beta",
        location="Toronto, ON",
        description="Python, FastAPI, Postgres.",
        work_mode="hybrid",
    )
    data.update(overrides)
    return Job(**data)


# --- filters + screening --------------------------------------------------------------


def test_hard_filters() -> None:
    user = _user()
    prefs = Preferences(excluded_companies=["Beta"], max_job_age_days=30)
    assert "skip Beta" in hard_filter_reason(user, _job(), prefs, user_requested=True)
    prefs = Preferences()
    assert hard_filter_reason(user, _job(), prefs, user_requested=False) is None
    old = _job(posted_at=datetime.now(timezone.utc) - timedelta(days=90))
    assert "Posted" in hard_filter_reason(user, old, prefs, user_requested=False)
    assert hard_filter_reason(user, old, prefs, user_requested=True) is None
    onsite = _job(work_mode="onsite")
    assert "Onsite" in hard_filter_reason(user, onsite, prefs, user_requested=False)


@pytest.mark.asyncio
async def test_screen_job_validates_model_output() -> None:
    user = _user()
    result = await screen_job(ScriptedProvider(), user, _job(), "Lin Ho, Python")
    assert (result.decision, result.score) == ("keep", 82)
    with pytest.raises(LLMError):
        await screen_job(ScriptedProvider(screen={"decision": "maybe"}), user, _job(), "x")


@pytest.mark.asyncio
async def test_tailor_removes_unverified_claims() -> None:
    user = _user()
    cv = await tailor_cv(ScriptedProvider(), user, _job(), "Lin Ho. Engineer at Acme. Python.")
    assert cv.resume.experience[0].bullets == ["Built FastAPI services"]
    assert cv.resume.skills == ["Python", "FastAPI"]
    assert set(cv.removed_claims) == {"Led a team of 40 engineers", "Kubernetes"}
    text = cv_to_text(cv.resume)
    assert "Built FastAPI services" in text and "40 engineers" not in text


@pytest.mark.asyncio
async def test_tailor_asks_for_the_base_resume_length() -> None:
    provider = ScriptedProvider()
    await tailor_cv(provider, _user(), _job(), "Lin Ho. Engineer at Acme. Python.", 2)
    assert "Length: at most 6 words, so it fits on 2 pages" in provider.prompts[0]
    await tailor_cv(provider, _user(), _job(), "Lin Ho. Python.")
    assert "fits on one page" in provider.prompts[2]


# --- ingest dedupe -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ingest_dedupes_by_canonical_url(monkeypatch) -> None:
    url = f"https://job-boards.greenhouse.io/beta/jobs/{time.time_ns()}"

    async def fake_fetch(u, client=None):
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="greenhouse",
            title=f"Platform Engineer {time.time_ns()}",
            company="Beta",
            description="Go and Kubernetes.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    user = _user()
    first = await ingest_module.ingest_url(user.id, url)
    second = await ingest_module.ingest_url(user.id, url + "?utm_source=linkedin")
    assert not first.duplicate and second.duplicate
    assert first.application_id == second.application_id

    with pytest.raises(ingest_module.IngestError):
        await ingest_module.ingest_url(user.id, "http://localhost:8000/x")


# --- full flow through chat ----------------------------------------------------------------


class FakeChannel:
    name = "telegram"

    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, chat_id, text, buttons=None, *, html=False):
        self.sent.append({"text": text, "buttons": buttons})
        return str(len(self.sent))

    async def send_document(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append({"text": caption, "document": Path(path), "buttons": buttons})
        return str(len(self.sent))

    async def send_photo(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append({"text": caption, "photo": Path(path), "buttons": buttons})
        return str(len(self.sent))

    async def clear_buttons(self, chat_id, message_id):
        pass

    async def answer_callback(self, callback_id, text=None):
        pass

    async def aclose(self):
        pass


@pytest.fixture
def chat(tmp_path, monkeypatch):
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
    user = _user()
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.add(MessagingLink(user_id=user.id, channel="telegram", chat_id="9", from_id="9"))
        session.commit()

    provider = ScriptedProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")

    async def fake_pdf(build, out_path, max_pages=1):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 " + build(0)[:40].encode())
        return 1

    monkeypatch.setattr(flow_module, "html_to_pdf", fake_pdf)

    async def fake_fetch(u, client=None):
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title="Backend Engineer",
            company=f"Beta{time.time_ns()}",
            location="Remote",
            work_mode="remote",
            salary_text="CAD 140k–160k",
            description="Python, FastAPI.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    enqueued: list[int] = []
    from app.apply.runs import run_manager

    monkeypatch.setattr(run_manager, "enqueue", lambda app_id, **kw: enqueued.append(app_id) or 1)

    service = MessagingService()
    service.channel = FakeChannel()
    flow = JobFlow(service)
    flow.register()
    yield SimpleNamespace(
        service=service, flow=flow, user=user, enqueued=enqueued, provider=provider
    )
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()


def _tap(data: str) -> dict:
    return {
        "callback_query": {
            "id": "cb",
            "from": {"id": 9},
            "data": data,
            "message": {"message_id": 3, "chat": {"id": 9}},
        }
    }


@pytest.mark.asyncio
async def test_link_to_card_then_apply(chat) -> None:
    await chat.flow.handle_link(chat.user.id, f"https://jobs.lever.co/beta/{time.time_ns()}")
    card = next(m for m in chat.service.channel.sent if "document" in m)
    assert card["document"].name == "Lin_Ho_Resume.pdf"
    assert "Match 82/100" in card["text"] and "CAD 140k" in card["text"]
    assert "Removed 2 claim(s)" in card["text"]
    assert chat.provider.calls == ["screen", "tailor", "fact_check"]

    apply_button = card["buttons"][0][0]
    skip_button = card["buttons"][0][1]
    await chat.service.router.handle_update(_tap(apply_button.data))
    assert len(chat.enqueued) == 1
    with SessionLocal() as session:
        app_row = session.get(Application, chat.enqueued[0])
        assert app_row.status == ApplicationStatus.approved
        assert app_row.resume_doc_id is not None
    # Skip on the same card is now spent.
    await chat.service.router.handle_update(_tap(skip_button.data))
    with SessionLocal() as session:
        assert session.get(Application, chat.enqueued[0]).status == ApplicationStatus.approved


@pytest.mark.asyncio
async def test_skip_with_company_reason_excludes_company(chat) -> None:
    await chat.flow.handle_link(chat.user.id, f"https://jobs.lever.co/beta/{time.time_ns()}")
    card = next(m for m in reversed(chat.service.channel.sent) if "document" in m)
    await chat.service.router.handle_update(_tap(card["buttons"][0][1].data))
    reasons = chat.service.channel.sent[-1]["buttons"]
    company_button = next(b for row in reasons for b in row if b.text == "This company")
    await chat.service.router.handle_update(_tap(company_button.data))
    assert "won't send" in chat.service.channel.sent[-1]["text"]
    with SessionLocal() as session:
        user = session.get(User, chat.user.id)
        company = session.query(Job).order_by(Job.id.desc()).first().company
        assert company in load_preferences(user).excluded_companies


@pytest.mark.asyncio
async def test_bad_link_is_explained(chat) -> None:
    await chat.flow.handle_link(chat.user.id, "http://192.168.1.4/admin")
    assert "doesn't look like" in chat.service.channel.sent[-1]["text"]
```

</details>
<!-- /code -->

### Discovery and the scheduler: `tests/test_discovery.py`

It proves stage 19: a search screens every candidate but prepares only your daily number of cards, the best first; a used-up budget is recorded; and the scheduler runs once a day, catches up after sleep, and respects pause.

The fakes: `ScoredProvider` extends `ScriptedProvider` and scores a job by the number in its title ("Backend Engineer 90" scores 90). `Discovery.gather` is replaced, so the "sources" return four fixed links. Each test builds its own `JobFlow` and `Discovery` objects, so it doesn't share state with the app's. For the scheduler, a `FakeSearch` records what would have started, and `tick(now)` gets a fixed time, which is why the scheduler takes `now` as an argument.

Three stories:

1. **`test_search_screens_all_and_sends_the_best_two`.** With `jobs_per_day=2`, four jobs are found and screened, three pass the minimum score of 60, and two cards are prepared: the 90 and the 75. A second search finds nothing new and prepares nothing. "Prepare more" then prepares the 65.
2. **`test_scheduler_tick_respects_pause_and_runs_once`.** At 10:00 with a 09:00 search time, a paused scheduler does nothing. Unpaused, it starts one `"schedule"` search and saves today's date, so it won't search again today.
3. **`test_only_live_readable_postings_become_jobs`.** Of five candidates, the closed, unreadable and nearly empty pages are dropped. The live page stays. The LinkedIn link from an alert email stays too, even though its page needs a login, because the email's snippet stands in for it.

<!-- code: backend/tests/test_discovery.py collapsed -->
<details>
<summary><code>backend/tests/test_discovery.py</code> (whole file, 307 lines)</summary>

```python
"""Job search: sources → dedupe → ingest → screen → best N cards; budgets; the daily schedule."""

from __future__ import annotations

import io
import time
from datetime import date, datetime
from datetime import time as dtime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from test_pipeline import ScriptedProvider

from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.llm.usage import BudgetExceededError
from app.main import app
from app.models import Application, ApplicationStatus, DiscoveryRun, Job, MessagingLink
from app.pipeline import discovery as discovery_module
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline import scheduler as scheduler_module
from app.pipeline.discovery import Discovery
from app.pipeline.flow import JobFlow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.prefs import Preferences, save_preferences
from app.pipeline.scheduler import Scheduler, is_due, next_search_at
from app.pipeline.sources.base import Candidate
from app.pipeline.state import set_paused


class ScoredProvider(ScriptedProvider):
    """Screens by title: the number in the title is the score."""

    async def chat(self, messages, **kwargs):
        system = messages[0].content
        if "screen job postings" in system:
            import json
            import re

            from app.llm.base import ChatResponse

            title = re.search(r"Title: (.*)", messages[1].content).group(1)
            score = int(re.search(r"(\d+)", title).group(1))
            self.calls.append(f"screen:{score}")
            body = {
                "decision": "keep" if score >= 60 else "drop",
                "score": score,
                "summary": "ok",
                "reasons": [],
                "gaps": [],
            }
            return ChatResponse(content=json.dumps(body), model="m", provider="s", raw={})
        return await super().chat(messages, **kwargs)


@pytest.fixture
def search(tmp_path, monkeypatch):
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        user = get_or_create_local_user(session)
        user.work_modes = ["remote"]
        user.target_roles = ["Backend Engineer"]
        save_preferences(
            user,
            Preferences(
                jobs_per_day=2, min_match_score=60, max_screens_per_run=10, web_search_enabled=False
            ),
        )
        # Start clean: nothing prepared today, nothing left over to screen or send.
        for app_row in session.execute(
            select(Application).where(Application.user_id == user.id)
        ).scalars():
            app_row.tailoring = None
            if app_row.status == ApplicationStatus.matched:
                app_row.status = ApplicationStatus.filtered_out
        session.commit()
        user_id = user.id
    set_paused(False)

    provider = ScoredProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")

    async def fake_pdf(build, out_path, max_pages=1):
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF")
        return 1

    monkeypatch.setattr(flow_module, "html_to_pdf", fake_pdf)
    stamp = time.time_ns()
    titles = {
        f"https://jobs.lever.co/acme/{stamp}-{s}": f"Backend Engineer {s}" for s in (90, 75, 65, 40)
    }

    async def fake_fetch(u, client=None):
        # Live, readable postings (cards left by other tests are rechecked too).
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title=titles.get(u, "Earlier job"),
            # Unique per run: ingest treats the same company + title as a duplicate.
            company=f"Co{u.rsplit('/', 1)[-1]}",
            work_mode="remote",
            description="Python and FastAPI services. " * 20,
        )

    monkeypatch.setattr(discovery_module, "fetch_job_details", fake_fetch)
    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)

    async def fake_gather(self, user, prefs, summary):
        found = [Candidate(url=u, source="watchlist", title=t) for u, t in titles.items()]
        summary.sources["watchlist"] = len(found)
        return self._dedupe(found, prefs)

    monkeypatch.setattr(Discovery, "gather", fake_gather)
    flow = JobFlow(flow_module.job_flow.messaging)
    return discovery_module.Discovery(flow), user_id, provider, titles


@pytest.mark.asyncio
async def test_search_screens_all_and_sends_the_best_two(search) -> None:
    finder, user_id, provider, titles = search
    run_id = await finder.run(user_id, "manual")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, run_id)
        assert (row.status, row.found, row.screened, row.kept, row.sent) == ("done", 4, 4, 3, 2)
        cards = (
            session.execute(
                select(Application)
                .join(Job, Job.id == Application.job_id)
                .where(
                    Application.user_id == user_id,
                    Application.status == ApplicationStatus.awaiting_approval,
                    Job.title.in_(titles.values()),
                )
            )
            .scalars()
            .all()
        )
        assert sorted(c.screen["score"] for c in cards) == [75, 90]
    assert sorted(c for c in provider.calls if c.startswith("screen:")) == [
        "screen:40",
        "screen:65",
        "screen:75",
        "screen:90",
    ]

    # Quota used up: a second search finds nothing new and sends nothing.
    again = await finder.run(user_id, "manual")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, again)
        assert (row.found, row.sent) == (0, 0)

    # "Prepare more" sends the shortlisted 65.
    more = await finder.more(user_id, 3)
    with SessionLocal() as session:
        assert session.get(DiscoveryRun, more).sent == 1


@pytest.mark.asyncio
async def test_budget_stop_is_recorded(search, monkeypatch) -> None:
    finder, user_id, _, _ = search

    async def broke(app_id, *, user_requested=False):
        raise BudgetExceededError("Today's Claude budget ($10.00) is used up.")

    monkeypatch.setattr(finder.flow, "screen", broke)
    run_id = await finder.run(user_id, "schedule")
    with SessionLocal() as session:
        row = session.get(DiscoveryRun, run_id)
    assert row.status == "stopped" and "budget" in row.error


def test_schedule_due_and_catch_up() -> None:
    at = dtime(9, 0)
    morning = datetime(2026, 10, 19, 8, 0).astimezone()
    late = datetime(2026, 10, 19, 15, 0).astimezone()
    assert not is_due(morning, at, None)
    assert is_due(late, at, date(2026, 10, 18))  # PC was asleep at 9:00 → catch up
    assert not is_due(late, at, date(2026, 10, 19))
    assert next_search_at(morning, at, None).hour == 9
    assert next_search_at(late, at, date(2026, 10, 19)).day == 20


def test_scheduler_tick_respects_pause_and_runs_once(monkeypatch) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.onboarded_at = datetime(2026, 1, 1)
        save_preferences(user, Preferences(discovery_time="09:00"))
        session.commit()
    monkeypatch.setattr(scheduler_module, "last_search_day", lambda: None)
    started: list[str] = []

    class FakeSearch:
        running = False

        def start(self, user_id, trigger):
            started.append(trigger)
            return True

    sched = Scheduler(FakeSearch())
    now = datetime(2026, 10, 19, 10, 0).astimezone()
    set_paused(True)
    assert sched.tick(now) is False
    set_paused(False)
    assert sched.tick(now) is True and started == ["schedule"]
    assert scheduler_module.get_flag(scheduler_module.LAST_SEARCH_KEY) == "2026-10-19"


@pytest.mark.asyncio
async def test_only_live_readable_postings_become_jobs(search, monkeypatch) -> None:
    """Search results whose page can't be read are dropped; alert emails keep their snippet."""

    from app.pipeline.jd import JobFetchError, PostingClosedError

    finder, user_id, _, _ = search
    stamp = time.time_ns()
    pages = {
        f"https://jobs.lever.co/live/{stamp}": "Python " * 80,
        f"https://jobs.lever.co/closed/{stamp}": PostingClosedError(),
        f"https://jobs.lever.co/unreadable/{stamp}": JobFetchError("timeout"),
        f"https://jobs.lever.co/thin/{stamp}": "Apply now",
        f"https://www.linkedin.com/jobs/view/{stamp}": JobFetchError("login"),
    }

    async def fake_fetch(u, client=None):
        page = pages[u]
        if isinstance(page, Exception):
            raise page
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title="Backend Engineer 80",
            company=f"Co{stamp}{u[-20:]}",
            description=page,
        )

    monkeypatch.setattr(discovery_module, "fetch_job_details", fake_fetch)
    candidates = [
        Candidate(url=u, source="web_search", title="Backend Engineer 80", company="Made Up Inc")
        for u in pages
        if "linkedin" not in u
    ] + [
        Candidate(
            url=f"https://www.linkedin.com/jobs/view/{stamp}",
            source="email",
            title="Backend Engineer 80",
            company=f"Alerted{stamp}",
            snippet="From a LinkedIn alert",
        )
    ]
    from app.pipeline.discovery import SearchSummary

    with SessionLocal() as session:
        user = session.get(__import__("app.models", fromlist=["User"]).User, user_id)
        session.expunge(user)
    app_ids = await finder.ingest(user, candidates, SearchSummary())
    with SessionLocal() as session:
        urls = sorted(session.get(Job, session.get(Application, i).job_id).url for i in app_ids)
    assert urls == sorted(
        [f"https://jobs.lever.co/live/{stamp}", f"https://www.linkedin.com/jobs/view/{stamp}"]
    )


@pytest.mark.asyncio
async def test_recheck_withdraws_cards_whose_posting_closed(search, monkeypatch) -> None:
    from app.pipeline.discovery import recheck_open_postings
    from app.pipeline.jd import PostingClosedError

    finder, user_id, _, titles = search
    await finder.run(user_id, "manual")
    with SessionLocal() as session:
        cards = session.execute(
            select(Application.id, Job.url)
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.user_id == user_id,
                Application.status == ApplicationStatus.awaiting_approval,
                Job.title.in_(titles.values()),
            )
        ).all()
    closed_url = cards[0][1]

    async def fetch(u, client=None):
        if u == closed_url:
            raise PostingClosedError()
        return JobDetails(url=u, canonical_url=canonicalize_url(u), ats="lever", title="x")

    monkeypatch.setattr(discovery_module, "fetch_job_details", fetch)
    gone = await recheck_open_postings(user_id)
    assert cards[0][0] in gone
    with SessionLocal() as session:
        app = session.get(Application, cards[0][0])
        assert app.status == ApplicationStatus.filtered_out
        assert app.stage_reason == "The posting was taken down."
```

</details>
<!-- /code -->

### The browser's view: `tests/test_home_api.py`

It proves stages 22 and 23 through HTTP, the way the web app uses them: paste a link, wait for the match card, Apply or Skip, open the PDF, retry a notice, change a style, save a Claude key, finish setup, pause.

The `ready` fixture puts a fake key in the in-memory secret store, fakes Claude, PDF printing and fetching, points `SERVE_ROOTS` at a temporary folder, and replaces `run_manager.enqueue` with a function that returns 4242. `_wait_for` polls `GET /api/home` until a condition holds, for up to 5 seconds. The two key tests swap `validate_claude_key` for a fake, so Anthropic is never called.

Three stories:

1. **`test_link_becomes_a_match_card_then_apply`.** `POST /api/jobs/link` answers 202 with `processing: true`. A match card appears with score 82, the reasons, and "Kubernetes" among the removed claims. The CV is served as `application/pdf`. Apply answers 200 with `run_id` 4242. **A second Apply is a 409**, and so is a Skip. The card is gone.
2. **`test_claude_key_is_checked_and_kept_out_of_responses`.** A bad key gets 400 and `GET /api/setup` still says `claude_key: false`. A good key gets 200 with `connected: true` and `source: "app"`. The key appears neither in that response nor in `GET /api/home`, but it is in the secret store.
3. **`test_failed_preparation_shows_a_notice_and_retries`.** A provider that raises "Your credit balance is too low" produces a notice card with `retry: true`. After the provider is fixed, `POST /api/agent/notices/{id}/retry` answers 200 and the match card appears.

Also worth reading: `test_cv_style_is_a_preference_and_a_card_can_change_it` checks that changing style never calls Claude, and that a skipped card can't change style any more (409).

<!-- code: backend/tests/test_home_api.py collapsed -->
<details>
<summary><code>backend/tests/test_home_api.py</code> (whole file, 423 lines)</summary>

```python
"""The browser flow: setup, preferences, paste a link → match card → Apply / Skip, files, pause."""

from __future__ import annotations

import io
import time

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import delete
from test_pipeline import ScriptedProvider

from app.api import documents as documents_api
from app.api import setup as setup_api
from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.main import app
from app.models import Application, ApplicationStatus, Document, DocumentKind, MessagingLink, User
from app.pipeline import flow as flow_module
from app.pipeline import ingest as ingest_module
from app.pipeline.flow import job_flow
from app.pipeline.jd import JobDetails, canonicalize_url
from app.pipeline.state import is_paused, set_paused
from app.services.secrets import ANTHROPIC_API_KEY, get_secret_store


def _wait_for(client: TestClient, predicate, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body: dict = {}
    while time.monotonic() < deadline:
        body = client.get("/api/home").json()
        if predicate(body):
            return body
        time.sleep(0.03)
    raise AssertionError(f"home never matched: {body.get('needs_you')}")


@pytest.fixture
def ready(tmp_path, monkeypatch):
    """A user with a resume and a Claude key; Claude, PDF rendering and fetching are faked."""

    get_secret_store().set(ANTHROPIC_API_KEY, "sk-ant-test-key-000000000000")
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        user = get_or_create_local_user(session)
        user.work_modes = ["remote", "hybrid"]
        user.target_roles = ["Backend Engineer"]
        user.target_locations = ["Toronto"]
        user.preferences = {}
        session.commit()
    set_paused(False)

    provider = ScriptedProvider()
    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(flow_module, "DOCS_DIR", tmp_path / "docs")
    monkeypatch.setattr(documents_api, "SERVE_ROOTS", [tmp_path])

    rendered: list[str] = []

    async def fake_pdf(build, out_path, max_pages=1):
        rendered.append(build(0))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"%PDF-1.4 tailored")
        return 1

    monkeypatch.setattr(flow_module, "html_to_pdf", fake_pdf)

    async def fake_fetch(u, client=None):
        return JobDetails(
            url=u,
            canonical_url=canonicalize_url(u),
            ats="lever",
            title="Backend Engineer",
            company=f"Beta{time.time_ns()}",
            location="Remote",
            work_mode="remote",
            description="Python, FastAPI.",
        )

    monkeypatch.setattr(ingest_module, "fetch_job_details", fake_fetch)
    enqueued: list[int] = []
    from app.apply.runs import run_manager

    monkeypatch.setattr(
        run_manager, "enqueue", lambda app_id, **kw: enqueued.append(app_id) or 4242
    )
    job_flow.notices.clear()
    with TestClient(app) as client:
        client.post(
            "/api/documents",
            data={"kind": "resume"},
            files={
                "file": ("cv.txt", io.BytesIO(b"Lin Ho. Engineer at Acme. Python."), "text/plain")
            },
        )
        client.enqueued = enqueued  # type: ignore[attr-defined]
        client.provider = provider  # type: ignore[attr-defined]
        client.rendered = rendered  # type: ignore[attr-defined]
        yield client


def _paste(client: TestClient) -> int:
    resp = client.post(
        "/api/jobs/link", json={"url": f"https://jobs.lever.co/beta/{time.time_ns()}"}
    )
    assert resp.status_code == 202, resp.text
    assert resp.json()["processing"] is True
    return resp.json()["application_id"]


def _match(body: dict, app_id: int) -> dict | None:
    return next(
        (c for c in body["needs_you"] if c["type"] == "match" and c["application_id"] == app_id),
        None,
    )


def test_link_becomes_a_match_card_then_apply(ready) -> None:
    app_id = _paste(ready)
    body = _wait_for(ready, lambda b: _match(b, app_id) is not None)
    card = _match(body, app_id)
    assert card["score"] == 82 and card["reasons"] == ["Python", "APIs"]
    assert card["changes"] and "Kubernetes" in card["removed_claims"]
    assert body["agent"]["sent_today"] >= 1

    pdf = ready.get(f"/api/documents/{card['resume_doc_id']}/file")
    assert pdf.status_code == 200 and pdf.content == b"%PDF-1.4 tailored"
    assert pdf.headers["content-type"] == "application/pdf"

    applied = ready.post(f"/api/applications/{app_id}/apply")
    assert applied.status_code == 200 and applied.json()["run_id"] == 4242
    assert ready.enqueued == [app_id]
    # Double click / the Telegram button after the UI: already handled.
    assert ready.post(f"/api/applications/{app_id}/apply").status_code == 409
    assert ready.post(f"/api/applications/{app_id}/skip", json={}).status_code == 409
    assert _match(ready.get("/api/home").json(), app_id) is None


def test_skip_with_company_reason_learns(ready) -> None:
    app_id = _paste(ready)
    _wait_for(ready, lambda b: _match(b, app_id) is not None)
    resp = ready.post(f"/api/applications/{app_id}/skip", json={"reason": "company"})
    assert resp.status_code == 200 and "won't send" in resp.json()["message"]
    detail = ready.get(f"/api/applications/{app_id}").json()
    assert detail["group"] == "skipped" and detail["stage_reason"] == "Skipped: This company"
    prefs = ready.get("/api/preferences").json()
    assert detail["job"]["company"] in prefs["excluded_companies"]


def test_failed_preparation_shows_a_notice_and_retries(ready, monkeypatch) -> None:
    from app.llm.base import LLMError

    class Broken(ScriptedProvider):
        async def chat(self, messages, **kwargs):
            raise LLMError("Claude error 400: Your credit balance is too low.")

    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: Broken())
    app_id = _paste(ready)
    body = _wait_for(
        ready,
        lambda b: any(
            c["type"] == "notice" and c["application_id"] == app_id for c in b["needs_you"]
        ),
    )
    notice = next(c for c in body["needs_you"] if c["type"] == "notice")
    assert "credit balance" in notice["text"] and notice["retry"] is True

    monkeypatch.setattr(flow_module, "get_llm_provider", lambda: ready.provider)
    assert ready.post(f"/api/agent/notices/{notice['id']}/retry").status_code == 200
    _wait_for(ready, lambda b: _match(b, app_id) is not None)


def test_cv_style_is_a_preference_and_a_card_can_change_it(ready) -> None:
    assert ready.get("/api/preferences").json()["document_style"] == "modern"
    assert ready.put("/api/preferences", json={"document_style": "fancy"}).status_code == 400
    saved = ready.put("/api/preferences", json={"document_style": "classic"}).json()
    assert saved["document_style"] == "classic"
    styles = {s["id"] for s in ready.get("/api/documents/styles").json()}
    assert {"modern", "classic", "minimal", "compact", "bold"} <= styles

    app_id = _paste(ready)
    card = _match(_wait_for(ready, lambda b: _match(b, app_id) is not None), app_id)
    assert card["style"] == "classic" and card["can_restyle"] is True
    resume_markup, letter_markup = ready.rendered[-2:]
    assert "Cambria" in resume_markup and "Re: Backend Engineer" in letter_markup

    claude_calls = len(ready.provider.calls)
    resp = ready.post(f"/api/applications/{app_id}/style", json={"style": "bold"})
    assert resp.status_code == 200 and "Bold" in resp.json()["message"]
    assert len(ready.provider.calls) == claude_calls  # restyling never calls Claude
    card = _match(ready.get("/api/home").json(), app_id)
    assert card["style"] == "bold" and card["changes"]
    assert "#1f2937" in ready.rendered[-2]
    assert ready.get(f"/api/documents/{card['resume_doc_id']}/file").status_code == 200
    assert ready.post(f"/api/applications/{app_id}/style", json={"style": "x"}).status_code == 400

    preview = ready.get("/api/documents/styles/minimal/preview?kind=cover_letter")
    assert preview.status_code == 200 and "text/html" in preview.headers["content-type"]
    assert "default-src 'none'" in preview.headers["content-security-policy"]
    assert "grid-template-columns" in preview.text and "Hiring Team" in preview.text
    assert ready.get("/api/documents/styles/nope/preview").status_code == 404

    ready.post(f"/api/applications/{app_id}/skip", json={})
    late = ready.post(f"/api/applications/{app_id}/style", json={"style": "modern"})
    assert late.status_code == 409


def test_card_from_a_replaced_resume_can_be_tailored_again(ready) -> None:
    app_id = _paste(ready)
    card = _match(_wait_for(ready, lambda b: _match(b, app_id) is not None), app_id)
    assert card["older_resume"] is False
    with SessionLocal() as session:
        row = session.get(Application, app_id)
        row.tailoring = {**row.tailoring, "prepared_at": "2026-01-01T00:00:00+00:00"}
        session.commit()
    ready.post(
        "/api/documents",
        data={"kind": "resume"},
        files={"file": ("cv2.txt", io.BytesIO(b"Lin Ho. Co-founder at Bee."), "text/plain")},
    )
    assert _match(ready.get("/api/home").json(), app_id)["older_resume"] is True

    claude_calls = len(ready.provider.calls)
    resp = ready.post(f"/api/applications/{app_id}/prepare")
    assert resp.status_code == 202 and "current resume" in resp.json()["message"]
    _wait_for(ready, lambda b: (_match(b, app_id) or {}).get("older_resume") is False)
    assert len(ready.provider.calls) > claude_calls


def test_bad_link_is_a_friendly_400(ready) -> None:
    resp = ready.post("/api/jobs/link", json={"url": "http://192.168.1.4/admin"})
    assert resp.status_code == 400
    assert "doesn't look like" in resp.json()["detail"]


def test_link_needs_claude_key(ready) -> None:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    resp = ready.post("/api/jobs/link", json={"url": "https://jobs.lever.co/beta/1"})
    assert resp.status_code == 400 and "Claude" in resp.json()["detail"]


def test_files_are_owned_and_confined(ready, tmp_path) -> None:
    outside = tmp_path.parent / f"outside-{time.time_ns()}.pdf"
    outside.write_bytes(b"%PDF secret")
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        stranger = User(name="Other")
        session.add(stranger)
        session.flush()
        mine = Document(
            user_id=user.id,
            kind=DocumentKind.resume,
            label="x",
            is_base=False,
            pdf_path=str(outside),
        )
        theirs = Document(
            user_id=stranger.id,
            kind=DocumentKind.resume,
            label="y",
            is_base=False,
            pdf_path=str(tmp_path / "theirs.pdf"),
        )
        (tmp_path / "theirs.pdf").write_bytes(b"%PDF theirs")
        session.add_all([mine, theirs])
        session.commit()
        ids, stranger_id = (mine.id, theirs.id), stranger.id
    try:
        assert ready.get(f"/api/documents/{ids[0]}/file").status_code == 404  # outside data/
        assert ready.get(f"/api/documents/{ids[1]}/file").status_code == 404  # not yours
    finally:
        with SessionLocal() as session:
            session.delete(session.get(Document, ids[0]))
            session.delete(session.get(User, stranger_id))  # other tests expect one user
            session.commit()


def test_preferences_round_trip(ready) -> None:
    resp = ready.put(
        "/api/preferences",
        json={
            "target_roles": ["Backend Engineer", " backend engineer ", "Platform Engineer"],
            "work_modes": ["remote"],
            "min_salary": 140000,
            "work_authorization": "yes_no_restrictions",
            "requires_sponsorship": "no",
            "jobs_per_day": 3,
            "discovery_time": "08:30",
            "web_search_enabled": False,
        },
    )
    assert resp.status_code == 200, resp.text
    body = ready.get("/api/preferences").json()
    assert body["target_roles"] == ["Backend Engineer", "Platform Engineer"]
    assert body["jobs_per_day"] == 3 and body["discovery_time"] == "08:30"
    assert body["requires_sponsorship"] == "no" and body["web_search_enabled"] is False
    assert ready.put("/api/preferences", json={"discovery_time": "25:00"}).status_code == 422
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        assert user.min_salary == 140000
        assert user.application_defaults["work_authorization"] == "yes_no_restrictions"


def test_claude_key_is_checked_and_kept_out_of_responses(ready, monkeypatch) -> None:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    calls: list[str] = []

    async def fake_validate(key: str, workspace_id: str = "") -> None:
        calls.append(key)
        if key.endswith("bad"):
            raise HTTPException(status_code=400, detail="Anthropic didn't accept that key.")

    monkeypatch.setattr(setup_api, "validate_claude_key", fake_validate)
    bad = ready.put("/api/setup/claude-key", json={"api_key": "sk-ant-api03-xxxxxxxxxxxxbad"})
    assert bad.status_code == 400
    assert ready.get("/api/setup").json()["claude_key"] is False

    key = "sk-ant-api03-goodgoodgoodgood"
    ok = ready.put("/api/setup/claude-key", json={"api_key": key})
    assert ok.status_code == 200 and ok.json()["connected"] is True
    assert ok.json()["source"] == "app"
    assert key not in ok.text and key not in ready.get("/api/home").text
    assert get_secret_store().get(ANTHROPIC_API_KEY) == key
    assert ready.get("/api/setup").json()["claude_key"] is True


def test_finish_setup_marks_onboarded_and_searches(ready, monkeypatch) -> None:
    from app.pipeline.discovery import discovery

    started: list[tuple[int, str]] = []
    monkeypatch.setattr(
        discovery,
        "start",
        lambda user_id, trigger="manual": started.append((user_id, trigger)) or True,
    )
    with SessionLocal() as session:
        get_or_create_local_user(session).onboarded_at = None
        session.commit()
    resp = ready.post("/api/setup/finish")
    assert resp.status_code == 200, resp.text
    assert started and started[0][1] == "manual"
    assert ready.get("/api/setup").json()["onboarded"] is True


def test_pause_and_resume(ready) -> None:
    assert ready.post("/api/agent/pause").status_code == 200
    assert is_paused() and ready.get("/api/home").json()["agent"]["paused"] is True
    assert ready.get("/api/home").json()["agent"]["next_search_at"] is None
    assert ready.post("/api/agent/resume").status_code == 200
    assert not is_paused()


def test_home_hides_unscreened_jobs_and_groups_the_rest(ready) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        from app.models import Job

        def add(status, screen=None):
            job = Job(
                source="test", external_id=str(time.time_ns()), url="https://x.test", title="T"
            )
            session.add(job)
            session.flush()
            session.add(Application(user_id=user.id, job_id=job.id, status=status, screen=screen))

        add(ApplicationStatus.matched)
        add(ApplicationStatus.matched, {"decision": "keep", "score": 90})
        add(ApplicationStatus.filtered_out)
        add(ApplicationStatus.submitted)
        session.commit()
    body = ready.get("/api/home").json()
    labels = {row["label"] for row in body["applications"]}
    assert {"Shortlisted", "Not a fit", "Applied"} <= labels
    assert "New" not in labels
    assert body["counts"]["applied"] >= 1 and body["agent"]["shortlisted"] >= 1


def test_org_wide_key_needs_and_sends_a_workspace(ready, monkeypatch) -> None:
    """Organization-wide keys must name a workspace on every request."""

    from app.llm.factory import get_llm_provider
    from app.pipeline.state import set_flag
    from app.services.secrets import WORKSPACE_ID_KEY

    seen: list[tuple[str, str]] = []

    async def fake_validate(key: str, workspace_id: str = "") -> None:
        seen.append((key, workspace_id))
        if not workspace_id:
            raise HTTPException(status_code=400, detail=setup_api.WORKSPACE_HINT)

    monkeypatch.setattr(setup_api, "validate_claude_key", fake_validate)
    try:
        # A new key without a workspace is refused with the explanation.
        org_key = "sk-ant-admin-org-wide-key-0000"
        resp = ready.put("/api/setup/claude-key", json={"api_key": org_key})
        assert resp.status_code == 400 and "workspace" in resp.json()["detail"]
        # Adding only the workspace keeps the current key and checks the pair.
        resp = ready.put("/api/setup/claude-key", json={"workspace_id": "wrkspc_01ABC"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["workspace_id"] == "wrkspc_01ABC"
        assert seen[-1] == ("sk-ant-test-key-000000000000", "wrkspc_01ABC")
        # Every Claude client now carries the header.
        from app.apply.agent import make_client

        assert make_client().default_headers.get("anthropic-workspace-id") == "wrkspc_01ABC"
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        from app.config import get_settings

        get_settings.cache_clear()
        provider = get_llm_provider()
        assert provider._client.default_headers.get("anthropic-workspace-id") == "wrkspc_01ABC"
        bad = ready.put("/api/setup/claude-key", json={"workspace_id": "bad id!"})
        assert bad.status_code == 422
    finally:
        set_flag(WORKSPACE_ID_KEY, "")
        monkeypatch.delenv("LLM_PROVIDER", raising=False)
        from app.config import get_settings
        from app.llm.factory import reset_llm_provider

        get_settings.cache_clear()
        reset_llm_provider()
```

</details>
<!-- /code -->

### Apply runs: `tests/test_apply_runs.py`

It runs the *real* run manager from stage 21, with its queue, statuses, site accounts, chat prompts, revisions, expiry and scorecard, through the apply-runs API. Only Chrome and Claude are fake:

- **`FakeSession`** stands in for the browser session: it has a tab id and does nothing.
- **`FakeDriver`** stands in for stage 17's guarded driver. `screenshot_png` writes a few fake PNG bytes and returns a review URL. `click_final_submit` counts its clicks and checks it got the reviewed URL. `page_text` returns "Thank you for applying!" unless a test says otherwise.
- **`fake_run_agent`** replaces stage 18's `run_agent`. Instead of calling Claude, it plays a script the test picks: `"plain"` finishes at once with three answers, `"account"` creates a site account first through `hooks.site_login`, and `"captcha"` asks for a person through `hooks.request_human`. The hooks are the real `RunHooks`, so everything they write is real.

`_wait` polls `GET /api/apply-runs/{id}` every 20 ms until the run reaches a status.

Three stories:

1. **`test_unattended_run_makes_an_account_then_review_change_and_submit`.** The run starts (202); a second start is refused. It parks at `awaiting_submit` with the answers and an expiry time. The site account used your profile email, and its password is only in the keyring, never in the run's events. The screenshot is served, and the review card lists "Salary" as a field to check. You change "Notice period" to "1 month"; the answer comes back with `source: "you"`. Submit returns `submitted` with the "Thank you for applying" text as confirmation. **A second decision gets 409**, the driver clicked exactly once, and the tab is closed. Your correction is saved for next time, but your email and the consent are not.
2. **`test_handoff_sets_aside_then_attended_retry_waits_for_the_user`.** With the CAPTCHA script, the unattended run can't ask anyone, so it ends `needs_you` and a set-aside card appears. "I'm at my PC" (`/attend`) creates a new, attended run; a second press on the old card gets 409. The new run waits at `needs_human`: the set-aside card is gone and a hand-off card is shown. `/resume` sends an empty note, and the run parks for review.
3. **`test_restart_fails_fills_and_expires_parked_forms`.** Three runs are put in by hand: `needs_human`, `awaiting_submit` and `revising`. After `mark_interrupted_runs()` they're `failed`, `expired` and `expired`, and the application is back to `approved`.

Also worth reading: `test_parked_form_expires` sets the time limit to 0.003 minutes (about 0.2 seconds), watches the form expire, and checks that a late Submit gets 409. `test_unconfirmed_submit_asks_the_user` makes the click fail and checks that the tab stays open for you.

<!-- code: backend/tests/test_apply_runs.py collapsed -->
<details>
<summary><code>backend/tests/test_apply_runs.py</code> (whole file, 539 lines)</summary>

```python
"""Apply-run lifecycle through the API and chat, with scripted stand-ins for browser + Claude.

Real: the queue, statuses, site accounts, chat prompts/buttons, the submit decision,
revisions, expiry and scorecard. Faked: the browser session/driver and the Claude
agent loop (each test scripts what "Claude" does through the run's hooks).
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from anthropic.tools import ToolError
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.api import documents as documents_api
from app.api.profile import get_or_create_local_user
from app.apply import runs as runs_module
from app.apply import scorecard as scorecard_module
from app.apply.accounts import account_password
from app.apply.agent import RunStats, request_params
from app.apply.runs import run_manager
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import (
    Application,
    ApplicationStatus,
    ApplyRun,
    Job,
    MessagingLink,
    SiteAccount,
    User,
)
from app.pipeline.prefs import load_preferences, save_preferences

REVIEW_URL = "https://acme.wd3.myworkdayjobs.com/en-US/External/job/123/apply/review"
HOST = "acme.wd3.myworkdayjobs.com"
ANSWERS = [
    {"field": "Email", "value": "lin@example.com", "source": "profile", "note": None},
    {"field": "Notice period", "value": "2 weeks", "source": "judgement", "note": "default"},
    {"field": "Terms", "value": "Accepted", "source": "consent", "note": "Privacy statement"},
]


class FakeSession:
    agent_focus_target_id = "tab-1"
    is_cdp_connected = True

    async def navigate_to(self, url, new_tab=False):
        self.url = url

    async def close_page(self, tab_id):
        pass

    async def kill(self):
        pass


class FakeDriver:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.clicks = 0

    async def __aenter__(self):
        return self

    async def close(self):
        pass

    def pin_to_tab(self, tab_id):
        self.tab = tab_id

    async def screenshot_png(self, tab_id, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"\x89PNG fake")
        return REVIEW_URL

    def network_mark(self):
        return {}

    def requests_since(self, mark):
        return [
            {"method": "POST", "url": "https://acme.wd3.myworkdayjobs.com/submit", "status": 200}
        ]

    async def click_final_submit(self, tab_id, reviewed_url):
        self.clicks += 1
        assert reviewed_url == REVIEW_URL
        return self.behaviour.get("click", (True, "Submit application"))

    async def page_text(self, tab_id):
        return REVIEW_URL, self.behaviour.get("page_text", "Thank you for applying!")


@pytest.fixture
def scripted(tmp_path, monkeypatch):
    """Patch the browser/Claude boundaries; returns knobs for the scripted agent.

    `behaviour["script"]`: "plain" (just finish), "account" (create a site account
    first) or "captcha" (ask for a human; set aside unless the run is attended).
    """

    behaviour: dict = {"script": "plain", "finish": "ready_for_review", "tasks": []}
    monkeypatch.setattr(runs_module, "RUNS_DIR", tmp_path / "runs")
    monkeypatch.setattr(scorecard_module, "SPIKE_DIR", tmp_path / "spike")
    monkeypatch.setattr(scorecard_module, "SCORECARD", tmp_path / "spike" / "scorecard.jsonl")
    monkeypatch.setattr(runs_module, "_CONFIRM_WAIT_S", 0.2)
    monkeypatch.setattr(documents_api, "SERVE_ROOTS", [tmp_path])

    async def fake_preflight(client, model, effort):
        return None

    async def fake_browser():
        return FakeSession()

    drivers: list[FakeDriver] = []

    def fake_guarded_driver(session, uploads_dir, hooks):
        assert any(Path(uploads_dir).iterdir()), "a resume should be staged"
        driver = FakeDriver(behaviour)
        drivers.append(driver)
        return driver

    async def fake_run_agent(client, driver, *, task, ats, config, hooks):
        behaviour["tasks"].append(task)
        hooks.event("tool_call", {"iteration": 1, "tool": "browser.navigate", "input": {}})
        if task.startswith("The application in this tab is already filled"):
            return RunStats(
                iterations=2,
                cost_usd=0.05,
                finish={"status": "ready_for_review", "summary": "Changed it.", "answers": []},
            )
        script = behaviour["script"]
        if script == "account":
            try:
                behaviour["login"] = hooks.site_login(HOST, "create")
            except ToolError as e:
                behaviour["refused"] = str(e)
                return RunStats(finish={"status": "needs_you", "summary": "Needs an account."})
        if script == "captcha":
            reply = await hooks.request_human("captcha", "Solve the CAPTCHA on the sign-up page")
            behaviour.setdefault("replies", []).append(reply)
            if reply is None:
                return RunStats(
                    iterations=4,
                    finish={"status": "needs_you", "summary": "Solve the CAPTCHA on sign-up."},
                )
        if behaviour["finish"] is None:
            return RunStats(iterations=3, error="Claude stopped without finishing (end_turn).")
        return RunStats(
            iterations=7,
            cost_usd=0.42,
            finish={
                "status": behaviour["finish"],
                "summary": "Filled every page.",
                "answers": ANSWERS,
                "fields_needing_review": ["Salary"],
            },
        )

    closed_tabs: list[str] = []

    async def fake_close_tab(tab_id):
        closed_tabs.append(tab_id)

    monkeypatch.setattr(runs_module, "make_client", lambda: object())
    monkeypatch.setattr(runs_module, "preflight", fake_preflight)
    monkeypatch.setattr(run_manager, "_browser", fake_browser)
    monkeypatch.setattr(run_manager, "_session", FakeSession())
    monkeypatch.setattr(run_manager, "_close_tab", fake_close_tab)
    monkeypatch.setattr(runs_module, "guarded_driver", fake_guarded_driver)
    monkeypatch.setattr(runs_module, "run_agent", fake_run_agent)
    behaviour["drivers"] = drivers
    behaviour["closed_tabs"] = closed_tabs
    behaviour["scorecard"] = tmp_path / "spike" / "scorecard.jsonl"
    return behaviour


def _seed(client: TestClient) -> int:
    client.post(
        "/api/documents",
        data={"kind": "resume"},
        files={"file": ("resume.txt", io.BytesIO(b"Lin Ho - backend engineer"), "text/plain")},
    )
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        user.email = "lin@example.com"
        save_preferences(user, load_preferences(user))
        job = Job(
            source="test",
            external_id=f"run-{time.time_ns()}",
            url=f"https://{HOST}/en-US/External/job/123",
            title="Backend Engineer",
            company="Acme",
        )
        session.add(job)
        session.flush()
        application = Application(user_id=user.id, job_id=job.id, status=ApplicationStatus.approved)
        session.add(application)
        session.execute(delete(SiteAccount))
        session.commit()
        return application.id


def _wait(client: TestClient, run_id: int, status: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    body = {}
    while time.monotonic() < deadline:
        body = client.get(f"/api/apply-runs/{run_id}").json()
        if body["run"]["status"] == status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} never reached {status!r}: {body.get('run')}")


def _app_status(client: TestClient, application_id: int) -> str:
    return client.get(f"/api/applications/{application_id}").json()["status"]


def _card(client: TestClient, kind: str) -> dict | None:
    return next((c for c in client.get("/api/home").json()["needs_you"] if c["type"] == kind), None)


def test_unattended_run_makes_an_account_then_review_change_and_submit(scripted) -> None:
    scripted["script"] = "account"
    with TestClient(app) as client:
        app_id = _seed(client)
        started = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert started.status_code == 202, started.text
        run_id = started.json()["id"]
        again = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert again.status_code in (400, 409)  # already running, or already parked

        parked = _wait(client, run_id, "awaiting_submit")
        assert parked["run"]["answers"] == ANSWERS
        assert parked["run"]["expires_at"] is not None
        assert _app_status(client, app_id) == "awaiting_submit"

        # The account: made with the profile email, password in the keyring only.
        login, password = scripted["login"]
        assert login == "lin@example.com" and len(password) == 20
        with SessionLocal() as session:
            user_id = get_or_create_local_user(session).id
            account = session.execute(select(SiteAccount)).scalar_one()
        assert (account.host, account.login, account.status) == (HOST, login, "created")
        assert account_password(user_id, HOST) == password
        events = [e["kind"] for e in parked["events"]]
        assert "account_created" in events
        assert password not in json.dumps(parked)
        assert "No saved account" not in scripted["tasks"][0]
        assert "JobBee has no account on this job's site yet" in scripted["tasks"][0]
        assert "Accept terms and privacy statements" in scripted["tasks"][0]

        shot = client.get(f"/api/apply-runs/{run_id}/screenshot")
        assert shot.status_code == 200 and shot.content.endswith(b"PNG fake")
        review = _card(client, "review")
        assert review["run_id"] == run_id and review["checks"] == ["Salary"]
        assert review["answers"][1]["source"] == "judgement" and review["updating"] is False

        # The user corrects the agent's judgement call before submitting.
        change = {"changes": [{"field": "Notice period", "value": "1 month"}]}
        assert client.post(f"/api/apply-runs/{run_id}/revise", json=change).status_code == 202
        assert client.post(f"/api/apply-runs/{run_id}/revise", json=change).status_code in (
            202,
            409,
        )
        revised = _wait(client, run_id, "awaiting_submit")
        assert scripted["tasks"][-1].startswith("The application in this tab is already filled")
        assert '"Notice period": 1 month' in scripted["tasks"][-1]
        notice = next(a for a in revised["run"]["answers"] if a["field"] == "Notice period")
        assert notice == {
            "field": "Notice period",
            "value": "1 month",
            "source": "you",
            "note": None,
        }
        assert revised["run"]["error"] is None

        decided = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert decided.status_code == 200, decided.text
        assert decided.json()["status"] == "submitted"
        assert decided.json()["confirmation"]["text"].lower().startswith("thank you for applying")
        assert _app_status(client, app_id) == "submitted"
        assert (
            client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True}).status_code
            == 409
        )
        assert scripted["drivers"][0].clicks == 1
        assert scripted["closed_tabs"] == ["tab-1"]  # applied: its tab is closed

        detail = client.get(f"/api/applications/{app_id}").json()
        assert detail["runs"][0]["answers"][1]["value"] == "1 month"

    with SessionLocal() as session:
        user = session.execute(select(User).order_by(User.id)).scalars().first()
        saved = user.application_defaults["saved_answers"]
        # Confirmed by Submit: the correction is remembered; profile answers aren't copied.
        assert saved["notice period"] == "1 month"
        assert "email" not in saved and "terms" not in saved
    row = json.loads(scripted["scorecard"].read_text().splitlines()[-1])
    assert row["outcome"] == "submitted" and row["reached_review"] is True


def test_handoff_sets_aside_then_attended_retry_waits_for_the_user(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]

        set_aside = _wait(client, run_id, "needs_you")
        assert scripted["replies"] == [None]  # nobody was asked; the run ended at once
        assert _app_status(client, app_id) == "approved"
        card = _card(client, "set_aside")
        assert card["run_id"] == run_id and "CAPTCHA" in card["message"]
        assert set_aside["run"]["attended"] is False

        attended = client.post(f"/api/apply-runs/{run_id}/attend")
        assert attended.status_code == 200, attended.text
        retry_id = attended.json()["id"]
        assert retry_id != run_id and attended.json()["attended"] is True
        assert client.post(f"/api/apply-runs/{run_id}/attend").status_code == 409

        waiting = _wait(client, retry_id, "needs_human")
        assert waiting["run"]["pending"]["kind"] == "captcha"
        assert _card(client, "set_aside") is None
        assert _card(client, "handoff")["run_id"] == retry_id
        assert client.post(f"/api/apply-runs/{retry_id}/resume", json={}).is_success

        _wait(client, retry_id, "awaiting_submit")
        assert scripted["replies"] == [None, ""]
        client.post(f"/api/apply-runs/{retry_id}/decision", json={"submit": False})


def test_set_aside_application_can_be_skipped(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        assert client.post(f"/api/apply-runs/{run_id}/dismiss").json()["status"] == "discarded"
        assert _app_status(client, app_id) == "rejected_by_user"
        assert client.post(f"/api/apply-runs/{run_id}/attend").status_code == 409


def test_accounts_need_the_users_permission(scripted) -> None:
    scripted["script"] = "account"
    with TestClient(app) as client:
        app_id = _seed(client)
        assert client.put("/api/preferences", json={"agent_accounts": False}).is_success
        assert client.get("/api/preferences").json()["agent_accounts"] is False
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        assert "hasn't allowed" in scripted["refused"]
        assert "create job-site accounts and sign in with `site_account`: not allowed" in (
            scripted["tasks"][0].lower()
        )
        assert client.put("/api/preferences", json={"agent_accounts": True}).is_success
    with SessionLocal() as session:
        assert session.execute(select(SiteAccount)).first() is None


def test_dont_submit_closes_and_marks_skipped(scripted) -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "awaiting_submit")
        resp = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": False})
        assert resp.json()["status"] == "discarded"
        assert _app_status(client, app_id) == "rejected_by_user"
        assert scripted["drivers"][0].clicks == 0
        late = client.post(
            f"/api/apply-runs/{run_id}/revise", json={"changes": [{"field": "a", "value": "b"}]}
        )
        assert late.status_code == 409


def test_unconfirmed_submit_asks_the_user(scripted) -> None:
    scripted["click"] = (False, "no final submit button found")
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "awaiting_submit")
        resp = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert resp.json()["status"] == "submit_unconfirmed"
        assert _app_status(client, app_id) == "awaiting_submit"
        assert scripted["closed_tabs"] == []  # left open for the user to check
        ok = client.post(f"/api/apply-runs/{run_id}/outcome", json={"went_through": True})
        assert ok.json()["status"] == "submitted"
        assert _app_status(client, app_id) == "submitted"


def test_parked_form_expires(scripted, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "submit_ttl_min", 0.003)  # ~0.2s
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "expired")
        assert _app_status(client, app_id) == "approved"
        late = client.post(f"/api/apply-runs/{run_id}/decision", json={"submit": True})
        assert late.status_code == 409


def test_blocked_run_marks_failed(scripted) -> None:
    scripted["finish"] = "blocked"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "blocked")
        assert _app_status(client, app_id) == "failed"
        detail = client.get(f"/api/applications/{app_id}").json()
        assert detail["group"] == "didnt_finish" and detail["can_retry"] is True
        retried = client.post(f"/api/applications/{app_id}/retry")
        assert retried.status_code == 200, retried.text
        _wait(client, retried.json()["run_id"], "blocked")
        assert scripted["closed_tabs"][0] == "tab-1"  # the first try's tab was closed


def test_cancel_while_waiting_on_the_user(scripted) -> None:
    scripted["script"] = "captcha"
    with TestClient(app) as client:
        app_id = _seed(client)
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        retry_id = client.post(f"/api/apply-runs/{run_id}/attend").json()["id"]
        _wait(client, retry_id, "needs_human")
        assert client.post(f"/api/apply-runs/{retry_id}/cancel").is_success
        cancelled = _wait(client, retry_id, "cancelled")
        assert cancelled["run"]["pending"] is None
        assert scripted["closed_tabs"] == ["tab-1"]  # the set-aside one; a stopped run keeps it
        again = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert again.status_code == 202
        client.post(f"/api/apply-runs/{again.json()['id']}/cancel")


def test_chat_reply_finishes_a_handoff(scripted) -> None:
    """A free-text Telegram reply resolves an attended run's hand-off."""

    from app.messaging.service import messaging

    scripted["script"] = "captcha"
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()
    with TestClient(app) as client:
        app_id = _seed(client)
        with SessionLocal() as session:
            user_id = get_or_create_local_user(session).id
            session.add(
                MessagingLink(user_id=user_id, channel="telegram", chat_id="77", from_id="77")
            )
            session.commit()
        run_id = client.post(f"/api/applications/{app_id}/apply-runs", json={}).json()["id"]
        _wait(client, run_id, "needs_you")
        retry_id = client.post(f"/api/apply-runs/{run_id}/attend").json()["id"]
        _wait(client, retry_id, "needs_human")

        update = {
            "message": {"message_id": 5, "chat": {"id": 77}, "from": {"id": 77}, "text": "done"}
        }
        client.portal.call(messaging.router.handle_update, update)
        _wait(client, retry_id, "awaiting_submit")
        assert scripted["replies"] == [None, ""]
        client.post(f"/api/apply-runs/{retry_id}/decision", json={"submit": False})
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.commit()


def test_start_requires_approved_application(scripted) -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
        with SessionLocal() as session:
            session.get(Application, app_id).status = ApplicationStatus.matched
            session.commit()
        resp = client.post(f"/api/applications/{app_id}/apply-runs", json={})
        assert resp.status_code == 400


def test_restart_fails_fills_and_expires_parked_forms() -> None:
    with TestClient(app) as client:
        app_id = _seed(client)
    with SessionLocal() as session:
        session.get(Application, app_id).status = ApplicationStatus.awaiting_submit
        rows = [
            ApplyRun(application_id=app_id, status=status, url="https://x", model="m", effort="low")
            for status in ("needs_human", "awaiting_submit", "revising")
        ]
        session.add_all(rows)
        session.commit()
        ids = [r.id for r in rows]

    assert run_manager.mark_interrupted_runs() >= 3
    with SessionLocal() as session:
        assert [session.get(ApplyRun, i).status for i in ids] == ["failed", "expired", "expired"]
        assert session.get(Application, app_id).status == ApplicationStatus.approved


@pytest.mark.asyncio
async def test_closing_the_last_tab_quits_chrome() -> None:
    calls: list[str] = []

    class TabsSession:
        def __init__(self, tabs: list[str]) -> None:
            targets = [SimpleNamespace(target_id=t) for t in tabs]
            self.session_manager = SimpleNamespace(get_all_page_targets=lambda: targets)

        async def close_page(self, tab_id: str) -> None:
            calls.append(f"close {tab_id}")

        async def kill(self) -> None:
            calls.append("quit")

    manager = runs_module.RunManager()
    manager._session = TabsSession(["T1", "T2"])  # another form is still open
    await manager._close_tab("T2")
    assert manager._session is not None
    manager._session = TabsSession(["T1"])
    await manager._close_tab("T1")
    assert calls == ["close T2", "quit"]
    assert manager._session is None  # the next run opens Chrome again


def test_requests_keep_the_cached_history_intact() -> None:
    params = request_params("claude-opus-5-5", "medium", "rules")
    assert "context_management" not in params
    assert params["cache_control"] == {"type": "ephemeral", "ttl": "1h"}


def test_agent_config_reports_models() -> None:
    with TestClient(app) as client:
        body = client.get("/api/apply-runs/config").json()
    assert "claude-opus-5-5" in body["models"]
    assert body["default_effort"] in body["efforts"]
```

</details>
<!-- /code -->

### Site accounts and the inbox: `tests/test_site_accounts.py`

It proves stage 20's accounts, stage 18's verification-email reader, stage 16's sensitive-field rule, and this stage's site-accounts API. Generated passwords meet every rule; the agent can create, sign in and reset only with your permission; a refused password is never tried again; only the site's own link or code is taken from an email.

The fakes: the in-memory secret store from `conftest.py`; a `FakeInbox` that returns prepared batches of emails, one per check; and `patch.object(client._http, "request", side_effect=fake_request)`, which makes the Gmail client's HTTP calls go to a function that returns ready-made `httpx.Response` objects. `@pytest.mark.parametrize` runs one test with eight different fields.

Three stories:

1. **`test_accounts_api_lists_reveals_and_forgets`.** The list shows host, login and status, and no password. Reveal returns exactly the password. Delete answers 204, the list is empty, a second reveal is 404, and the keyring entry is gone.
2. **`test_extract_takes_only_the_sites_own_link`.** A Workday email holds a link to `evil.example.com` and the real activation link. Only the site's own link is taken, with `&amp;` turned back into `&`. Asking the same email for a password reset finds nothing.
3. **`test_a_refused_password_is_never_tried_again`.** After the site rejects a password, signing in raises an error that tells the agent to use "Forgot password". After a reset, sign-in uses the new password.

<!-- code: backend/tests/test_site_accounts.py collapsed -->
<details>
<summary><code>backend/tests/test_site_accounts.py</code> (whole file, 248 lines)</summary>

```python
"""Job-site accounts: passwords, permissions, the verification-email reader and the API."""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from anthropic.tools import ToolError
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.profile import get_or_create_local_user
from app.apply import inbox
from app.apply.accounts import account_password, credentials, generate_password
from app.apply.dossier import _permissions
from app.apply.guards import (
    PASSWORD_FIELD_MESSAGE,
    sensitive_field_message,
)
from app.db import SessionLocal
from app.main import app
from app.models import SiteAccount
from app.pipeline.prefs import load_preferences, save_preferences
from app.services.email_client import GmailClient, RawEmailMessage

HOST = "acme.wd5.myworkdayjobs.com"
SINCE = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def user_id() -> int:
    with SessionLocal() as session:
        session.execute(delete(SiteAccount))
        user = get_or_create_local_user(session)
        user.email = "lin@example.com"
        prefs = load_preferences(user)
        prefs.agent_accounts = True
        save_preferences(user, prefs)
        session.commit()
        return user.id


def test_generated_passwords_meet_every_rule() -> None:
    for _ in range(50):
        password = generate_password()
        assert len(password) == 20
        assert any(c in string.ascii_uppercase for c in password)
        assert any(c in string.ascii_lowercase for c in password)
        assert any(c in string.digits for c in password)
        assert any(c in "!@#$%&*" for c in password)
    assert generate_password() != generate_password()


def test_credentials_create_sign_in_and_reset(user_id) -> None:
    with pytest.raises(ToolError, match="no saved account"):
        credentials(user_id, HOST, "sign_in")
    login, created, kind = credentials(user_id, HOST, "create")
    assert (login, kind) == ("lin@example.com", "account_created")
    assert account_password(user_id, HOST) == created
    assert credentials(user_id, HOST.upper(), "sign_in") == (login, created, "account_signed_in")
    _, reset, kind = credentials(user_id, HOST, "set_new_password")
    assert kind == "password_reset" and reset != created
    assert account_password(user_id, HOST) == reset


def test_credentials_respect_the_permission(user_id) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        prefs = load_preferences(user)
        prefs.agent_accounts = False
        save_preferences(user, prefs)
        session.commit()
    with pytest.raises(ToolError, match="hasn't allowed"):
        credentials(user_id, HOST, "create")
    assert account_password(user_id, HOST) is None


def test_a_refused_password_is_never_tried_again(user_id) -> None:
    credentials(user_id, HOST, "create")
    assert credentials(user_id, HOST, "rejected") == ("lin@example.com", "", "account_rejected")
    with pytest.raises(ToolError, match="Forgot password"):
        credentials(user_id, HOST, "sign_in")
    _, reset, _ = credentials(user_id, HOST, "set_new_password")
    assert credentials(user_id, HOST, "sign_in")[1] == reset


def test_dossier_tells_the_agent_about_the_account_and_inbox(user_id) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        refused = "\n".join(_permissions(user, "rejected", False))
        fresh = "\n".join(_permissions(user, "none", True))
    assert "Forgot password" in refused and "NOT connected" in refused
    assert "no account" in fresh and "password reset): connected" in fresh


def test_accounts_api_lists_reveals_and_forgets(user_id) -> None:
    _, password, _ = credentials(user_id, HOST, "create")
    with TestClient(app) as client:
        rows = client.get("/api/site-accounts").json()
        assert [(r["host"], r["login"], r["status"]) for r in rows] == [
            (HOST, "lin@example.com", "created")
        ]
        assert password not in str(rows)
        account_id = rows[0]["id"]
        assert client.post(f"/api/site-accounts/{account_id}/reveal").json() == {
            "password": password
        }
        assert client.delete(f"/api/site-accounts/{account_id}").status_code == 204
        assert client.get("/api/site-accounts").json() == []
        assert client.post(f"/api/site-accounts/{account_id}/reveal").status_code == 404
    assert account_password(user_id, HOST) is None


@pytest.mark.parametrize(
    ("field", "blocked"),
    [
        ({"type": "password", "name": "Password"}, PASSWORD_FIELD_MESSAGE),
        ({"type": "text", "name": "Verify New Password"}, PASSWORD_FIELD_MESSAGE),
        ({"type": "text", "name": "Social Insurance Number (SIN)"}, "social insurance number"),
        ({"type": "text", "name": "Bank account number"}, "bank"),
        ({"type": "text", "name": "", "autocomplete": "cc-number"}, "card details"),
        ({"type": "text", "name": "First Name | firstName"}, None),
        ({"type": "email", "name": "Email Address"}, None),
        (None, None),
    ],
)
def test_sensitive_fields(field, blocked) -> None:
    message = sensitive_field_message(field)
    if blocked is None:
        assert message is None
    else:
        assert message is not None and blocked.lower() in message.lower()


# --- the verification-email reader -----------------------------------------------------------

WORKDAY_VERIFY = RawEmailMessage(
    provider_message_id="m1",
    subject="Verify your candidate account",
    sender="Acme Careers <acme@myworkday.com>",
    received_at=SINCE,
    snippet="",
    body=(
        "<p>Please confirm your email.</p>"
        "<a href='https://evil.example.com/verify?t=1'>Not this one</a>"
        "<a href='https://acme.wd5.myworkdayjobs.com/en-US/External/activate?token=abc&amp;x=1'>"
        "Verify Account</a>"
    ),
)
CODE_MAIL = RawEmailMessage(
    provider_message_id="m2",
    subject="Your sign-in code",
    sender="no-reply@myworkday.com",
    received_at=SINCE,
    snippet="",
    body="<p>Your verification code is <b>482913</b>. It expires in 10 minutes.</p>",
)
PHISH = RawEmailMessage(
    provider_message_id="m3",
    subject="Verify your account",
    sender="support@myworkday-careers.com",
    received_at=SINCE,
    snippet="",
    body="<a href='https://acme.wd5.myworkdayjobs.com/activate?token=zzz'>Verify</a>",
)


def test_extract_takes_only_the_sites_own_link() -> None:
    found = inbox.extract(WORKDAY_VERIFY, HOST, "verify_email")
    assert found.link == "https://acme.wd5.myworkdayjobs.com/en-US/External/activate?token=abc&x=1"
    assert found.code is None and found.sender == "acme@myworkday.com"
    assert inbox.extract(WORKDAY_VERIFY, HOST, "reset_password") is None


def test_extract_codes_and_ignores_other_senders() -> None:
    assert inbox.extract(CODE_MAIL, HOST, "sign_in_code").code == "482913"
    assert inbox.extract(PHISH, HOST, "verify_email") is None
    assert inbox.extract(WORKDAY_VERIFY, "jobs.other.com", "verify_email") is None


def test_trusted_domains() -> None:
    assert inbox.trusted_domains(HOST) == ["myworkday.com", "myworkdayjobs.com", "workday.com"]
    assert inbox.trusted_domains("careers.acme.co.uk") == ["acme.co.uk"]


class FakeInbox:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def search_recent(self, *, senders, since, max_messages=10):
        self.calls.append((senders, since))
        return self.batches.pop(0) if self.batches else []


@pytest.mark.asyncio
async def test_wait_for_email_polls_until_it_arrives(monkeypatch, user_id) -> None:
    client = FakeInbox([[], [PHISH], [CODE_MAIL]])
    monkeypatch.setattr(inbox, "inbox_client", lambda session, uid: (client, None))
    found = await inbox.wait_for_email(user_id, HOST, "sign_in_code", SINCE, interval=0.01)
    assert found.code == "482913" and len(client.calls) == 3
    skipped = await inbox.wait_for_email(
        user_id, HOST, "sign_in_code", SINCE, skip={"m2"}, timeout=0.05, interval=0.01
    )
    assert skipped is None


@pytest.mark.asyncio
async def test_wait_for_email_needs_an_inbox(user_id) -> None:
    with pytest.raises(inbox.InboxUnavailable, match="No inbox"):
        await inbox.wait_for_email(user_id, HOST, "verify_email", SINCE, timeout=0)


def test_gmail_search_recent_queries_the_site_senders() -> None:
    client = GmailClient(client_id="cid", client_secret="secret", refresh_token="refresh")
    seen: list[dict] = []

    def fake_request(method, url, **kwargs):
        url = str(url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(
                200, json={"access_token": "t"}, request=httpx.Request(method, url)
            )
        if url.endswith("/users/me/messages"):
            seen.append(kwargs["params"])
            return httpx.Response(
                200, json={"messages": [{"id": "x1"}]}, request=httpx.Request(method, url)
            )
        return httpx.Response(
            200,
            json={
                "payload": {
                    "headers": [{"name": "From", "value": "a@myworkday.com"}],
                    "mimeType": "text/plain",
                    "body": {"data": "aGVsbG8="},
                }
            },
            request=httpx.Request(method, url),
        )

    since = SINCE - timedelta(minutes=10)
    with patch.object(client._http, "request", side_effect=fake_request):
        messages = client.search_recent(senders=["myworkday.com", "workday.com"], since=since)
    assert [m.body for m in messages] == ["hello"]
    assert seen[0]["q"] == (f"after:{int(since.timestamp())} from:(myworkday.com OR workday.com)")
```

</details>
<!-- /code -->

### Milestone 1: the backend is complete

Run the whole suite from the `backend` folder:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

On Mac or Linux:

```bash
.venv/bin/python -m pytest -q
```

You should see something like this (the time varies):

```text
.........s.............................................................. [ 32%]
........................................................................ [ 64%]
........................................................................ [ 97%]
...sss                                                                   [100%]
218 passed, 4 skipped in 12.84s
```

The four skipped tests drive a real browser. Three in `test_spike_driver.py` (stage 17) look for Google Chrome in its Windows install folders. One in `test_apply_guard_probe.py` (stage 16) needs any Chromium browser that Playwright can start. Add `-rs` to the command to see each reason. On a Windows PC with Chrome installed they run instead of skipping, so you see fewer skips and more passes.

Every piece now works together. Here is the whole path of one application, from your Apply click to a submitted form:

```mermaid
sequenceDiagram
  participant You
  participant API as FastAPI
  participant Flow as job_flow
  participant RM as run_manager
  participant Agent as apply agent (Opus)
  participant Chrome as guarded driver and Chrome
  You->>API: POST /api/applications/7/apply
  API->>Flow: approve(7)
  Flow->>Flow: awaiting_approval to approved (atomic)
  Flow->>RM: enqueue(7)
  RM-->>API: run 12 queued
  API-->>You: 200 with run_id 12
  RM->>Agent: the worker starts run 12
  Agent->>Chrome: fill every page (submit clicks blocked)
  Agent-->>RM: finish ready_for_review
  RM->>RM: park the form, screenshot, awaiting_submit
  You->>API: GET /api/home shows the review card
  You->>API: POST /api/apply-runs/12/decision (submit true)
  API->>RM: decide(12, submit=True)
  RM->>Chrome: click_final_submit, once
  Chrome-->>RM: clicked, confirmation page found
  RM-->>API: submitted
  API-->>You: 200, status submitted
```

#### Try it by hand

You can drive the finished backend from `/docs` before any web page exists. You need a real Anthropic key with a little credit.

1. **Start the backend without `--reload`:**

   ```powershell
   .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

2. **Open http://127.0.0.1:8000/docs.** You'll see the groups from the router table above.
3. **Save your key.** Under **setup**, open `PUT /api/setup/claude-key`, click **Try it out**, and send `{"api_key": "sk-ant-..."}`. The answer has `"connected": true` and `"source": "app"`, and no key. This check is free. A made-up key of at least 20 characters gets 400 "Anthropic didn't accept that key." and nothing is saved.
4. **Import your resume.** Under **profile**, `POST /api/profile/import` with your resume file in `resume_file` (stage 7). This saves it as your base resume. It makes one Claude call (Haiku) to read it.
5. **Paste a job link.** Under **agent**, `POST /api/jobs/link` with `{"url": "..."}` and a real Greenhouse or Lever posting. You get 202 and an `application_id`.
6. **Watch `GET /api/home`.** Run it every few seconds. First `needs_you` holds a `preparing` card with `"stage": "screening"`, then `"tailoring"`. After a minute or so it becomes a `match` card with a score, reasons and a `resume_doc_id`. Open http://127.0.0.1:8000/api/documents/RESUME_DOC_ID/file, with the card's `resume_doc_id` in place of `RESUME_DOC_ID`, to see the tailored PDF.

If you want to go all the way, `POST /api/applications/{id}/apply` opens Chrome and fills the form, which costs about $0.50 to $1. When `GET /api/home` shows a `review` card, open http://127.0.0.1:8000 followed by its `screenshot_url` to see the filled form. Then send `{"submit": false}` to `POST /api/apply-runs/{run_id}/decision`. The tab closes and nothing is sent.

<!-- verify: backend | python -m pytest -q -->

## Common mistakes

- **Putting `/apply-runs/{run_id}` before `/apply-runs/config`.** FastAPI tries routes in the order they're defined. If the `{run_id}` route came first, `GET /api/apply-runs/config` would try to read "config" as a run id and answer 422. Keep fixed paths above paths with parameters.
- **Answering with the request's stale copy of a run.** The run manager writes through its own sessions. Without `session.expire_all()` in `_fresh`, the decision endpoint can answer `awaiting_submit` for a run that's already `submitted`.
- **Saving the key before testing it, or sending it back.** Test first, then save. And never `return payload`, log the key, or add it to a status model. `test_home_api.py` checks that the key appears neither in the setup answer nor in `GET /api/home`.
- **Expecting "Forget key" to disconnect you.** It only deletes the keyring copy. If `backend/.env` also has `ANTHROPIC_API_KEY`, the status switches to `"source": "env"` and Claude still works.
- **Starting uvicorn with `--host 0.0.0.0`.** That makes the API, including password reveal, reachable from other devices on your network. Keep `--host 127.0.0.1`.
- **Testing `PUT /api/setup/budget` without care.** `save_env` writes to the real `backend/.env`; the temporary test database doesn't protect it. In a test, first point `settings_api.ENV_PATH` at a file in `tmp_path` with `monkeypatch`.

## Recap

- `applications.py` serves the drawer and the card buttons. Apply goes through `job_flow.approve`, so a second click is a 409. Prepare, retry and restyle each refuse cleanly when the application isn't in the right state.
- `apply_runs.py` wraps one `run_manager` method per endpoint, checks ownership through the application, returns fresh state with `expire_all()`, and serves the screenshot through `confined_file`.
- `setup.py` tests a Claude key with a free `count_tokens` call before saving it to the keyring, never sends it back, and writes spending limits to `.env` with `save_env`.
- `site_accounts.py` can reveal a site password only because it's your own login, it takes a click, and the API listens only on localhost.
- The final `main.py` mounts fifteen routers and runs the lifespan: database, chat handlers, run recovery, Telegram, scheduler; then shuts down in the reverse spirit.
- The tests fake only Claude, Chrome, the network and Telegram, and run everything else for real.
- `pytest -q` shows `218 passed, 4 skipped`: **Milestone 1, the backend is complete.**

**Next:** [Stage 24: Frontend basics](24-frontend-basics.md)
