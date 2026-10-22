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
