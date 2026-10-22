"""The home page in one call: agent status, what needs the user, what's applying, history.

GET /api/home
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..apply.runs import FILL_STATUSES
from ..db import get_session
from ..llm.usage import spend_summary
from ..messaging.service import messaging
from ..models import (
    Application,
    ApplicationStatus,
    ApplyEvent,
    ApplyRun,
    DiscoveryRun,
    DocumentKind,
    Job,
    User,
)
from ..pipeline.discovery import STAGES, discovery, prepared_today, shortlist
from ..pipeline.flow import job_flow
from ..pipeline.prefs import load_preferences
from ..pipeline.scheduler import last_search_day, next_search_at, parse_time
from ..pipeline.sources.email_alerts import inbox_connected
from ..pipeline.state import is_paused
from ..schemas import (
    AgentStatus,
    ApplicationRow,
    ApplyingRow,
    HomeResponse,
    JobCardInfo,
    LastSearch,
    MatchCard,
    NoticeCard,
    PreparingCard,
    RunCard,
    SetupStatus,
)
from ..services.documents import get_base_document
from ..services.secrets import claude_api_key
from .profile import get_or_create_local_user

router = APIRouter(tags=["home"])

_APPLICATION_ROWS = 300
_RUN_CARD_TYPES = {
    "awaiting_submit": "review",
    "revising": "review",
    "needs_human": "handoff",
    "needs_you": "set_aside",
    "submit_unconfirmed": "unconfirmed",
}
_CARD_ORDER = {"review": 0, "handoff": 1, "unconfirmed": 2, "set_aside": 3, "notice": 4}
_RUN_LABELS = {
    "queued": "Queued",
    "starting": "Applying",
    "running": "Applying",
    "needs_human": "Needs you at your PC",
    "needs_you": "Needs you at your PC",
    "revising": "Updating answers",
    "submitting": "Submitting",
    "expired": "Form timed out",
    "cancelled": "Stopped",
    "discarded": "Not submitted",
}


def job_info(job: Job) -> JobCardInfo:
    return JobCardInfo(
        id=job.id,
        title=job.title,
        company=job.company,
        location=job.location,
        work_mode=job.work_mode,
        salary_text=job.salary_text,
        url=job.url,
    )


def status_view(app: Application, run_status: str | None) -> tuple[str | None, str]:
    """(tab, label) for an application in the history list; tab None hides it."""

    status = app.status
    if status == ApplicationStatus.submitted:
        return "applied", "Applied"
    if status == ApplicationStatus.awaiting_submit:
        return "in_progress", "Ready to submit"
    if status == ApplicationStatus.awaiting_approval:
        return "in_progress", "Waiting for your OK"
    if status == ApplicationStatus.approved:
        return "in_progress", _RUN_LABELS.get(run_status or "", "Approved")
    if status == ApplicationStatus.matched:
        if app.screen and app.screen.get("decision") == "keep":
            return "in_progress", "Shortlisted"
        return None, "New"
    if status == ApplicationStatus.rejected_by_user:
        return "skipped", "Skipped"
    if status == ApplicationStatus.filtered_out:
        return "not_a_fit", "Not a fit"
    return "didnt_finish", "Didn't finish"


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _prepared_at(tailoring: dict | None) -> datetime | None:
    try:
        stamp = (tailoring or {}).get("prepared_at")
        return datetime.fromisoformat(stamp) if stamp else None
    except ValueError:
        return None


def can_restyle(app: Application) -> bool:
    """The CV can change style while nothing has been sent with it yet."""

    if not app.resume_doc_id:
        return False
    if (app.tailoring or {}).get("cv"):
        return app.status in (
            ApplicationStatus.awaiting_approval,
            ApplicationStatus.approved,
            ApplicationStatus.failed,
        )
    # Made before styles existed: only a card can be rewritten (one Claude call).
    return app.status == ApplicationStatus.awaiting_approval


def match_card(app: Application, job: Job, resume_since: datetime | None = None) -> MatchCard:
    """`resume_since`: when the current resume was uploaded; a CV tailored before then was
    made from the previous one."""

    screen = app.screen or {}
    tailoring = app.tailoring or {}
    prepared_at = _prepared_at(app.tailoring)
    return MatchCard(
        application_id=app.id,
        job=job_info(job),
        score=screen.get("score"),
        decision=screen.get("decision"),
        summary=screen.get("summary"),
        reasons=list(screen.get("reasons") or []),
        gaps=list(screen.get("gaps") or []),
        changes=list(tailoring.get("changes") or []),
        removed_claims=list(tailoring.get("removed_claims") or []),
        resume_doc_id=app.resume_doc_id,
        cover_doc_id=app.cover_doc_id,
        style=tailoring.get("style"),
        can_restyle=can_restyle(app),
        prepared_at=prepared_at,
        older_resume=bool(resume_since and prepared_at and _aware(prepared_at) < resume_since),
    )


def run_card(run: ApplyRun, job: Job) -> RunCard:
    kind = _RUN_CARD_TYPES[run.status]
    pending = run.pending or {}
    finish = run.finish or {}
    confirmation = run.confirmation or {}
    message = {
        "handoff": pending.get("message"),
        "set_aside": finish.get("summary") or run.error,
        "unconfirmed": confirmation.get("detail"),
    }.get(kind)
    return RunCard(
        type=kind,  # type: ignore[arg-type]
        run_id=run.id,
        application_id=run.application_id,
        job=job_info(job),
        handoff_kind=pending.get("kind") if kind == "handoff" else None,
        updating=run.status == "revising",
        error=run.error if kind == "review" else None,
        message=message,
        summary=finish.get("summary"),
        checks=list(finish.get("fields_needing_review") or []),
        answers=list(run.answers or finish.get("answers") or []),
        screenshot_url=f"/api/apply-runs/{run.id}/screenshot" if run.screenshot_path else None,
        expires_at=_aware(run.expires_at),
        clicked=confirmation.get("clicked") if kind == "unconfirmed" else None,
    )


def _looking_for(user: User) -> str:
    roles = ", ".join((user.target_roles or [])[:3]) or user.headline or "jobs that fit your resume"
    places = list(user.target_locations or [])[:3]
    if "remote" in (user.work_modes or []) and "Remote" not in places:
        places.append("Remote")
    return f"{roles} · {', '.join(places)}" if places else roles


def _agent_status(session: Session, user: User) -> AgentStatus:
    prefs = load_preferences(user)
    paused = is_paused()
    last = session.execute(
        select(DiscoveryRun)
        .where(DiscoveryRun.user_id == user.id, DiscoveryRun.trigger != "more")
        .order_by(DiscoveryRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    spend = spend_summary()
    budget = spend["daily_budget_usd"]
    next_at = None
    if not paused and user.onboarded_at is not None:
        now_local = datetime.now().astimezone()
        next_at = next_search_at(now_local, parse_time(prefs.discovery_time), last_search_day())
    return AgentStatus(
        paused=paused,
        searching=discovery.running,
        search_stage=STAGES.get(discovery.stage or "") if discovery.running else None,
        next_search_at=next_at,
        last_search=LastSearch(
            trigger=last.trigger,
            status=last.status,
            found=last.found,
            kept=last.kept,
            sent=last.sent,
            error=last.error,
            started_at=_aware(last.started_at),
            finished_at=_aware(last.finished_at),
        )
        if last
        else None,
        looking_for=_looking_for(user),
        sent_today=prepared_today(user.id),
        jobs_per_day=prefs.jobs_per_day,
        shortlisted=len(shortlist(user.id, prefs.min_match_score)),
        spend_today_usd=round(spend["today_usd"], 2),
        daily_budget_usd=budget,
        budget_reached=bool(budget) and spend["today_usd"] >= budget,
    )


def setup_status(session: Session, user: User) -> SetupStatus:
    resume = get_base_document(session, user.id, DocumentKind.resume)
    return SetupStatus(
        claude_key=bool(claude_api_key()),
        resume=resume is not None and bool((resume.content or "").strip()),
        onboarded=user.onboarded_at is not None,
        telegram=messaging.linked(user.id) is not None,
        inbox=inbox_connected(session, user),
    )


@router.get("/home", response_model=HomeResponse)
def home(session: Session = Depends(get_session)) -> HomeResponse:
    user = get_or_create_local_user(session)

    runs = session.execute(
        select(ApplyRun, Application, Job)
        .join(Application, Application.id == ApplyRun.application_id)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.user_id == user.id,
            ApplyRun.status.in_(set(_RUN_CARD_TYPES) | FILL_STATUSES | {"queued", "submitting"}),
        )
        .order_by(ApplyRun.id)
    ).all()
    latest = dict(
        session.execute(
            select(ApplyRun.application_id, func.max(ApplyRun.id))
            .where(ApplyRun.application_id.in_({app.id for _, app, _ in runs}))
            .group_by(ApplyRun.application_id)
        ).all()
    )
    cards: list = []
    applying: list[ApplyingRow] = []
    for run, app, job in runs:
        if run.status == "needs_you":
            # Set aside until the user is at their PC; gone once retried or skipped.
            if latest.get(app.id) == run.id and app.status == ApplicationStatus.approved:
                cards.append(run_card(run, job))
            continue
        if run.status == "needs_human":
            cards.append(run_card(run, job))
        elif run.status in _RUN_CARD_TYPES:
            cards.append(run_card(run, job))
            continue
        last_event = session.execute(
            select(ApplyEvent)
            .where(
                ApplyEvent.run_id == run.id, ApplyEvent.kind.in_(("tool_call", "text", "status"))
            )
            .order_by(ApplyEvent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        applying.append(
            ApplyingRow(
                run_id=run.id,
                application_id=app.id,
                job=job_info(job),
                status=run.status,
                last_event={"kind": last_event.kind, "data": last_event.data}
                if last_event
                else None,
                started_at=_aware(run.created_at),
            )
        )
    for notice in job_flow.notices:
        if notice.user_id == user.id:
            cards.append(
                NoticeCard(
                    id=notice.id,
                    text=notice.text,
                    level=notice.level,
                    application_id=notice.application_id,
                    retry=notice.retry,
                    created_at=notice.created_at,
                )
            )
    cards.sort(key=lambda c: _CARD_ORDER.get(c.type, 9))

    matches = session.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(
            Application.user_id == user.id,
            Application.status == ApplicationStatus.awaiting_approval,
        )
        .order_by(Application.updated_at.desc())
    ).all()
    resume = get_base_document(session, user.id, DocumentKind.resume)
    resume_since = _aware(resume.created_at) if resume else None
    match_cards = [
        match_card(app, job, resume_since)
        for app, job in matches
        if app.tailoring and app.id not in job_flow.stages
    ]
    match_cards.sort(
        key=lambda c: c.prepared_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True
    )
    cards += match_cards

    preparing_ids = [app_id for app_id in job_flow.stages]
    if preparing_ids:
        for app, job in session.execute(
            select(Application, Job)
            .join(Job, Job.id == Application.job_id)
            .where(Application.id.in_(preparing_ids), Application.user_id == user.id)
        ).all():
            stage = job_flow.stages.get(app.id)
            if stage:
                cards.append(PreparingCard(application_id=app.id, job=job_info(job), stage=stage))

    latest_runs = dict(
        session.execute(
            select(ApplyRun.application_id, ApplyRun.status).order_by(ApplyRun.id)
        ).all()
    )
    rows: list[ApplicationRow] = []
    counts = {"applied": 0, "in_progress": 0, "skipped": 0, "not_a_fit": 0, "didnt_finish": 0}
    apps = session.execute(
        select(Application, Job)
        .join(Job, Job.id == Application.job_id)
        .where(Application.user_id == user.id)
        .order_by(Application.updated_at.desc())
    ).all()
    for app, job in apps:
        group, label = status_view(app, latest_runs.get(app.id))
        if group is None:
            continue
        counts[group] += 1
        if len(rows) >= _APPLICATION_ROWS:
            continue
        score = (app.screen or {}).get("score")
        rows.append(
            ApplicationRow(
                id=app.id,
                job=job_info(job),
                status=app.status.value,
                group=group,  # type: ignore[arg-type]
                label=label,
                stage_reason=app.stage_reason,
                score=int(score) if score is not None else None,
                updated_at=_aware(app.updated_at),
                submitted_at=_aware(app.submitted_at),
            )
        )
    counts["needs_you"] = len([c for c in cards if c.type != "preparing"])

    return HomeResponse(
        setup=setup_status(session, user),
        agent=_agent_status(session, user),
        needs_you=cards,
        applying=applying,
        counts=counts,
        applications=rows,
    )
