"""Apply runs: a persistent queue, one agent at a time, filled forms parked for the user's OK.

Lifecycle of a run (ApplyRun.status):
  queued → starting → running (⇄ needs_human, attended runs only)  (filling; one at a time)
         → awaiting_submit   form filled, tab parked, card 2 sent (≤ MAX_PARKED at once)
             ⇄ revising      changing answers the user corrected on card 2
         → submitting → submitted | submit_unconfirmed           (user tapped Submit)
         → discarded                                             (user tapped Don't submit)
         → expired                                               (no answer within the TTL)
  or needs_you (set aside: only a human at the PC can go on, e.g. a CAPTCHA)
  or blocked | failed | cancelled while filling.

Runs are unattended: the agent signs in, answers and consents by itself (within
the user's permissions) and never waits on the user. Something only a person
can do sets the application aside; the user's "I'm at my PC" queues an attended
run, which may hand them the browser.

The agent never submits. After the user's Submit tap (atomic state change),
the server asks the parked driver to click the page's single final-submit
control once (`GuardedBrowserUse.click_final_submit`) and then looks for a
confirmation. Runs execute inside the API process: start the backend without
`--reload` while applying.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update

from ..config import BACKEND_DIR, get_settings
from ..db import SessionLocal
from ..models import (
    Application,
    ApplicationStatus,
    ApplyEvent,
    ApplyRun,
    Document,
    DocumentKind,
    Job,
    User,
)
from ..pipeline.sources.email_alerts import inbox_connected
from ..pipeline.state import is_paused
from ..services.documents import get_base_document
from . import accounts, inbox
from .agent import (
    AgentConfig,
    AgentHooks,
    RunStats,
    guarded_driver,
    make_client,
    preflight,
    run_agent,
)
from .answers import remember_answer
from .ats import detect_ats
from .browser import new_browser_session
from .costs import PRICES
from .dossier import UploadFile, build_dossier
from .guards import CONFIRMATION_RE
from .inbox import InboxFind
from .scorecard import append_scorecard, submit_request_rows

logger = logging.getLogger(__name__)

RUNS_DIR = BACKEND_DIR / "data" / "apply_runs"
FILL_STATUSES = {"starting", "running", "needs_user", "needs_human"}
PARKED_STATUSES = {"awaiting_submit", "revising"}
OPEN_STATUSES = {"queued", *FILL_STATUSES, *PARKED_STATUSES, "submitting"}
_TEXT_LIMIT = 2000
_CONFIRM_WAIT_S = 20.0
_HUMAN_WAIT_S = 15 * 60.0  # an attended run waits this long for the human
_REVISE_ITERATIONS = 30
_DONE_WORDS = {"done", "ok", "okay", "finished", "did it", "yes", "✅"}
# Answers the user confirms by tapping Submit, saved for the next application.
_LEARNED_SOURCES = {"judgement", "you"}


class RunConflictError(RuntimeError):
    """The application already has an open run."""


class RunStateError(RuntimeError):
    """The run isn't in a state that accepts this action."""


@dataclass
class PreparedRun:
    url: str
    ats: str
    task: str
    uploads_dir: Path
    company: str
    title: str


@dataclass
class RunContext:
    run_id: int
    application_id: int
    user_id: int
    config: AgentConfig
    task: asyncio.Task | None = None
    waiter: asyncio.Future | None = None
    waiter_message_id: str | None = None
    driver: Any = None
    tab_id: str | None = None
    reviewed_url: str | None = None
    expiry: asyncio.Task | None = None
    company: str = ""
    title: str = ""
    network_mark: dict = field(default_factory=dict)
    attended: bool = False
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    used_emails: set[str] = field(default_factory=set)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: Any) -> Any:
    if isinstance(value, str) and len(value) > _TEXT_LIMIT:
        return value[:_TEXT_LIMIT] + "..."
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clip(v) for v in value]
    return value


def build_task(url: str, dossier_text: str) -> str:
    return (
        f"Apply to this job on the applicant's behalf: {url}\n"
        "The browser is already open on that page. Fill the application completely from the "
        "dossier below, then hand it back with `finish`.\n\n" + dossier_text
    )


def build_revise_task(changes: list[dict]) -> str:
    lines = "\n".join(f'- "{c["field"]}": {c["value"]}' for c in changes)
    return (
        "The application in this tab is already filled and on its final review page. The "
        "applicant checked it and wants these answers changed:\n"
        f"{lines}\n\n"
        "Change only these: go to the page with each field (use the review page's edit links "
        "or Back), set the new value exactly, then return to the final review page. Don't "
        "change anything else and never submit. Then call `finish` with status "
        '"ready_for_review", listing only the fields you changed in `answers`. If a field '
        'can\'t be changed, call `finish` with status "blocked" and say why.'
    )


def merge_answers(answers: list[dict], changes: list[dict]) -> list[dict]:
    """The run's answers with the user's corrections applied (source "you")."""

    merged = [dict(a) for a in answers]
    for change in changes:
        key = change["field"].strip().lower()
        row = next((a for a in merged if str(a.get("field", "")).strip().lower() == key), None)
        if row is None:
            row = {"field": change["field"]}
            merged.append(row)
        row.update(value=change["value"], source="you", note=None)
    return merged


def site_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _messaging():
    from ..messaging.service import messaging

    return messaging


class RunHooks(AgentHooks):
    """Persists the agent's events; looks up site accounts and site emails; and, in attended
    runs only, hands the browser to the user."""

    def __init__(self, manager: RunManager, ctx: RunContext) -> None:
        self._manager = manager
        self._ctx = ctx

    def event(self, kind: str, data: dict[str, Any]) -> None:
        self._manager._add_event(self._ctx.run_id, kind, data)
        if kind == "usage":
            self._manager._update(
                self._ctx.run_id, iterations=data["iteration"], cost_usd=data["cost_usd"]
            )

    async def _wait(self, status: str, pending: dict, text: str, buttons) -> str | None:
        """The user's reply, or None if none came within `_HUMAN_WAIT_S`."""

        ctx = self._ctx
        ctx.waiter = asyncio.get_running_loop().create_future()
        self._manager._update(ctx.run_id, status=status, pending=pending)
        messaging = _messaging()

        async def on_reply(user_id: int, reply: str) -> None:
            self._manager.answer(ctx.run_id, reply, from_chat=True)

        messaging.router.expect_reply(ctx.user_id, on_reply)
        ctx.waiter_message_id = await messaging.notify(
            ctx.user_id, text, buttons=buttons, html=True, kind=status, run_id=ctx.run_id
        )
        try:
            return await asyncio.wait_for(ctx.waiter, _HUMAN_WAIT_S)
        except TimeoutError:
            return None
        finally:
            ctx.waiter = None
            messaging.router.cancel_reply(ctx.user_id, on_reply)
            await messaging.clear_buttons(ctx.user_id, ctx.waiter_message_id)
            self._manager._update(ctx.run_id, status="running", pending=None)

    async def request_human(self, kind: str, message: str) -> str | None:
        ctx = self._ctx
        if not ctx.attended:
            return None
        messaging = _messaging()
        group = messaging.new_group()
        buttons = [
            [
                messaging.button(
                    ctx.user_id,
                    "✅ Done",
                    "run_answer",
                    target_id=ctx.run_id,
                    payload={"value": ""},
                    group=group,
                ),
                messaging.button(
                    ctx.user_id,
                    "❌ Can't do it",
                    "run_answer",
                    target_id=ctx.run_id,
                    payload={"value": "The user could not do it right now."},
                    group=group,
                ),
            ]
        ]
        text = (
            f"🖥 <b>Needs you at your PC</b> ({escape(ctx.company)}):\n{escape(message[:900])}\n\n"
            "Do it in the JobBee Chrome window, then tap Done."
        )
        reply = await self._wait(
            "needs_human",
            {"type": "human_action", "kind": kind, "message": message},
            text,
            buttons,
        )
        if reply is None:
            return None
        return "" if reply.strip().lower() in _DONE_WORDS else reply

    def site_login(self, host: str, action: str) -> tuple[str, str]:
        login, password, kind = accounts.credentials(self._ctx.user_id, host, action)
        self.event(kind, {"host": host, "login": login})
        return login, password

    async def find_email(self, host: str, purpose: str) -> InboxFind | None:
        ctx = self._ctx
        since = ctx.started_at - timedelta(minutes=10)
        found = await inbox.wait_for_email(
            ctx.user_id, host, purpose, since, skip=frozenset(ctx.used_emails)
        )
        if found is None:
            self.event("email_checked", {"host": host, "purpose": purpose, "found": False})
            return None
        ctx.used_emails.add(found.message_id)
        if purpose == "verify_email":
            with SessionLocal() as session:
                accounts.mark(session, ctx.user_id, host, status="verified")
        self.event(
            "email_checked",
            {
                "host": host,
                "purpose": purpose,
                "found": True,
                "sender": found.sender,
                "kind": "link" if found.link else "code",
            },
        )
        return found


class RunManager:
    def __init__(self) -> None:
        self._worker: asyncio.Task | None = None
        self._active: RunContext | None = None
        self._parked: dict[int, RunContext] = {}
        self._slot_freed: asyncio.Event | None = None
        self._revisions: deque[int] = deque()  # parked runs with answers to change
        self._session = None  # browser-use BrowserSession, open while a form's tab is
        self._session_has_run = False
        self._browser_lock = asyncio.Lock()  # opening a tab vs. quitting Chrome

    # --- persistence helpers --------------------------------------------------------------

    @staticmethod
    def _update(run_id: int, **values: Any) -> None:
        with SessionLocal() as session:
            session.execute(update(ApplyRun).where(ApplyRun.id == run_id).values(**values))
            session.commit()

    @staticmethod
    def _add_event(run_id: int, kind: str, data: dict[str, Any]) -> None:
        with SessionLocal() as session:
            session.add(ApplyEvent(run_id=run_id, kind=kind, data=_clip(data)))
            session.commit()

    @staticmethod
    def _set_app(application_id: int, status: ApplicationStatus, reason: str | None = None) -> None:
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            if app is None:
                return
            app.status = status
            if reason is not None:
                app.stage_reason = reason
            if status == ApplicationStatus.submitted:
                app.submitted_at = _now()
            session.commit()

    def _transition(self, run_id: int, src: set[str] | str, dst: str, **values: Any) -> bool:
        """Atomic status change; False if the run wasn't in `src` (double taps, races)."""

        sources = {src} if isinstance(src, str) else src
        with SessionLocal() as session:
            result = session.execute(
                update(ApplyRun)
                .where(ApplyRun.id == run_id, ApplyRun.status.in_(sources))
                .values(status=dst, **values)
            )
            session.commit()
            return result.rowcount == 1

    # --- lifecycle ------------------------------------------------------------------------

    def mark_interrupted_runs(self) -> int:
        """After a restart nothing in flight can resume: fills fail, parked forms expire."""

        now = _now()
        with SessionLocal() as session:
            failed = session.execute(
                update(ApplyRun)
                .where(ApplyRun.status.in_(FILL_STATUSES | {"submitting"}))
                .values(
                    status="failed",
                    pending=None,
                    error="The backend restarted during this run.",
                    finished_at=now,
                )
            ).rowcount
            parked = (
                session.execute(select(ApplyRun).where(ApplyRun.status.in_(PARKED_STATUSES)))
                .scalars()
                .all()
            )
            for run in parked:
                run.status = "expired"
                run.pending = None
                app = session.get(Application, run.application_id)
                if app is not None and app.status == ApplicationStatus.awaiting_submit:
                    app.status = ApplicationStatus.approved
                    app.stage_reason = "The filled form was lost when JobBee restarted."
            session.commit()
            return (failed or 0) + len(parked)

    async def startup(self) -> None:
        self.mark_interrupted_runs()
        self.kick()

    async def shutdown(self) -> None:
        tasks = [self._worker]
        if self._active and self._active.task:
            tasks.append(self._active.task)
        tasks += [ctx.expiry for ctx in self._parked.values()]
        for task in tasks:
            if task and not task.done():
                task.cancel()
        await asyncio.gather(*(t for t in tasks if t), return_exceptions=True)
        for ctx in list(self._parked.values()):
            await self._close_driver(ctx)
        self._parked.clear()
        if self._session is not None:
            try:
                await self._session.kill()
            except Exception:  # pragma: no cover - best effort on shutdown
                logger.exception("Closing the apply browser failed")
            self._session = None

    # --- queue ----------------------------------------------------------------------------

    def enqueue(
        self,
        application_id: int,
        *,
        model: str | None = None,
        effort: str | None = None,
        attended: bool = False,
    ) -> int:
        settings = get_settings()
        model = model or settings.apply_model
        if model not in PRICES:
            raise ValueError(f"Unknown model {model!r}.")
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            if app is None:
                raise LookupError("Application not found.")
            if app.status not in (ApplicationStatus.approved, ApplicationStatus.awaiting_approval):
                raise ValueError("Only approved applications can be applied to.")
            open_run = session.execute(
                select(ApplyRun.id).where(
                    ApplyRun.application_id == application_id,
                    ApplyRun.status.in_(OPEN_STATUSES),
                )
            ).first()
            if open_run is not None:
                raise RunConflictError("This application is already being worked on.")
            app.status = ApplicationStatus.approved
            job = session.get(Job, app.job_id)
            run = ApplyRun(
                application_id=app.id,
                status="queued",
                url=job.url,
                ats=detect_ats(job.url),
                model=model,
                effort=effort or settings.apply_effort,
                attended=attended,
            )
            session.add(run)
            session.commit()
            run_id = run.id
        self.kick()
        return run_id

    def retry(self, application_id: int) -> int:
        """Queue a fresh run for an application whose last run failed or timed out."""

        with SessionLocal() as session:
            app = session.get(Application, application_id)
            if app is not None and app.status == ApplicationStatus.failed:
                app.status = ApplicationStatus.approved
                session.commit()
        return self.enqueue(application_id)

    def attend(self, run_id: int) -> int:
        """The user is at their PC: retry a set-aside application; it may hand them the browser."""

        with SessionLocal() as session:
            run = session.get(ApplyRun, run_id)
            if run is None or run.status != "needs_you":
                raise RunStateError("This application isn't waiting for you.")
            latest = session.execute(
                select(func.max(ApplyRun.id)).where(ApplyRun.application_id == run.application_id)
            ).scalar_one()
            if latest != run_id:
                raise RunStateError("This application was already tried again.")
            application_id = run.application_id
        return self.enqueue(application_id, attended=True)

    def dismiss(self, run_id: int) -> None:
        """Don't apply after all to a set-aside application."""

        if not self._transition(run_id, "needs_you", "discarded"):
            raise RunStateError("This application isn't waiting for you.")
        with SessionLocal() as session:
            application_id = session.get(ApplyRun, run_id).application_id
        self._set_app(application_id, ApplicationStatus.rejected_by_user, "You skipped it.")

    def kick(self) -> None:
        if self._slot_freed is not None:
            self._slot_freed.set()
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._worker_loop())

    async def _worker_loop(self) -> None:
        self._slot_freed = asyncio.Event()
        while True:
            if self._revisions:  # the user is waiting on these; they come first
                ctx = self._parked.get(self._revisions.popleft())
                if ctx is not None:
                    await self._revise(ctx)
                continue
            if is_paused():
                return  # queued runs wait; resuming kicks the worker again
            if len(self._parked) >= get_settings().max_parked_runs:
                self._slot_freed.clear()
                await self._slot_freed.wait()
                continue
            with SessionLocal() as session:
                run = (
                    session.execute(
                        select(ApplyRun)
                        .where(ApplyRun.status == "queued")
                        .order_by(ApplyRun.attended.desc(), ApplyRun.id)
                    )
                    .scalars()
                    .first()
                )
                if run is None:
                    return
                app = session.get(Application, run.application_id)
                settings = get_settings()
                ctx = RunContext(
                    run_id=run.id,
                    application_id=run.application_id,
                    user_id=app.user_id,
                    config=AgentConfig(
                        model=run.model,
                        effort=run.effort,
                        max_iterations=settings.apply_max_iterations,
                        max_cost_usd=settings.apply_max_cost_usd,
                    ),
                    attended=run.attended,
                )
            if not self._transition(ctx.run_id, "queued", "starting"):
                continue
            self._active = ctx
            ctx.task = asyncio.get_running_loop().create_task(self._fill(ctx))
            try:
                await asyncio.gather(ctx.task, return_exceptions=True)
            finally:
                self._active = None

    # --- public actions (API + chat) ------------------------------------------------------

    def answer(self, run_id: int, text: str, *, from_chat: bool = False) -> None:
        ctx = self._active
        if ctx is None or ctx.run_id != run_id or ctx.waiter is None or ctx.waiter.done():
            raise RunStateError("The agent isn't waiting for you right now.")
        ctx.waiter.set_result(text)

    async def cancel(self, run_id: int) -> None:
        if self._transition(run_id, "queued", "cancelled", finished_at=_now()):
            return
        ctx = self._active
        if ctx is not None and ctx.run_id == run_id and ctx.task and not ctx.task.done():
            ctx.task.cancel()
            return
        if run_id in self._parked:
            await self.decide(run_id, submit=False)
            return
        raise RunStateError("This run isn't running.")

    async def focus(self, run_id: int) -> None:
        ctx = self._parked.get(run_id) or (
            self._active if self._active and self._active.run_id == run_id else None
        )
        if self._session is None or ctx is None or ctx.tab_id is None:
            raise RunStateError("The browser tab for this run isn't open.")
        cdp = await self._session.get_or_create_cdp_session(ctx.tab_id, focus=True)
        await cdp.cdp_client.send.Page.bringToFront(session_id=cdp.session_id)

    async def decide(self, run_id: int, *, submit: bool) -> str:
        """The user's answer to card 2. Returns the run's resulting status."""

        target = "submitting" if submit else "discarded"
        if not self._transition(run_id, "awaiting_submit", target):
            raise RunStateError("This form was already handled or has expired.")
        ctx = self._parked.pop(run_id, None)
        self._free_slot()
        if ctx is None:
            self._update(run_id, status="expired", finished_at=_now())
            raise RunStateError("The filled form isn't open any more.")
        if ctx.expiry and not ctx.expiry.done():
            ctx.expiry.cancel()
        messaging = _messaging()
        with SessionLocal() as session:
            card_id = session.get(ApplyRun, run_id).submit_card_message_id
        await messaging.clear_buttons(ctx.user_id, card_id)

        if not submit:
            await self._close_driver(ctx, close_tab=True)
            self._update(run_id, pending=None)
            self._update(run_id, finished_at=_now())
            self._set_app(
                ctx.application_id,
                ApplicationStatus.rejected_by_user,
                "You chose not to submit after reviewing the form.",
            )
            self._score(run_id)
            await messaging.notify(
                ctx.user_id, f"OK — not submitting {ctx.company}.", run_id=ctx.run_id
            )
            return "discarded"

        await messaging.notify(ctx.user_id, f"🚀 Submitting {ctx.company}…", run_id=ctx.run_id)
        self._learn(run_id, ctx.user_id)
        status = await self._submit(ctx)
        self._score(run_id)
        return status

    async def resolve_unconfirmed(self, run_id: int, *, went_through: bool) -> None:
        dst = "submitted" if went_through else "failed"
        if not self._transition(run_id, "submit_unconfirmed", dst, finished_at=_now()):
            raise RunStateError("Nothing to confirm for this run.")
        with SessionLocal() as session:
            run = session.get(ApplyRun, run_id)
            app_id, user_id = (
                run.application_id,
                session.get(Application, run.application_id).user_id,
            )
        if went_through:
            self._set_app(app_id, ApplicationStatus.submitted)
            await _messaging().notify(user_id, "✅ Marked as applied.", run_id=run_id)
        else:
            self._set_app(app_id, ApplicationStatus.failed, "Submission didn't go through.")
            await _messaging().notify(user_id, "Noted — marked as not submitted.", run_id=run_id)

    # --- filling --------------------------------------------------------------------------

    async def _fill(self, ctx: RunContext) -> None:
        hooks = RunHooks(self, ctx)
        try:
            prepared = self._prepare(ctx)
            ctx.company, ctx.title = prepared.company, prepared.title
            client = make_client()
            await preflight(client, ctx.config.model, ctx.config.effort)

            async with self._browser_lock:
                session = await self._browser()
                await session.navigate_to(prepared.url, new_tab=self._session_has_run)
                self._session_has_run = True
                ctx.tab_id = session.agent_focus_target_id
            self._update(ctx.run_id, status="running", tab_id=ctx.tab_id)
            hooks.event("status", {"status": "running", "url": prepared.url})

            driver = guarded_driver(session, prepared.uploads_dir, hooks)
            await driver.__aenter__()
            driver.pin_to_tab(ctx.tab_id)
            try:
                stats = await run_agent(
                    client,
                    driver,
                    task=prepared.task,
                    ats=prepared.ats,
                    config=ctx.config,
                    hooks=hooks,
                )
            except BaseException as e:
                ctx.driver = driver
                # A run you stopped keeps its tab, in case you stopped it to take over.
                await self._close_driver(ctx, close_tab=not isinstance(e, asyncio.CancelledError))
                raise
            self._record_stats(ctx.run_id, stats)
            if stats.finish and stats.finish["status"] == "ready_for_review":
                await self._park(ctx, driver, stats)
                return
            ctx.driver = driver
            if stats.outcome == "needs_you":
                await self._close_driver(ctx, close_tab=True)
                await self._set_aside(ctx, stats)
                return
            await self._close_driver(ctx, close_tab=True)
            reason = (stats.finish or {}).get("summary") or stats.error or "It got stuck."
            self._set_app(ctx.application_id, ApplicationStatus.failed, reason)
            self._score(ctx.run_id)
            await self._notify_failure(ctx, reason)
        except asyncio.CancelledError:
            self._update(ctx.run_id, status="cancelled", pending=None, finished_at=_now())
            self._add_event(ctx.run_id, "status", {"status": "cancelled"})
            self._set_app(ctx.application_id, ApplicationStatus.approved, "Stopped.")
        except Exception as e:  # surfaced in chat and the UI
            logger.exception("Apply run %s failed", ctx.run_id)
            message = str(e) or e.__class__.__name__
            self._update(
                ctx.run_id, status="failed", pending=None, error=message, finished_at=_now()
            )
            self._add_event(ctx.run_id, "error", {"message": message})
            self._set_app(ctx.application_id, ApplicationStatus.failed, message)
            await self._notify_failure(ctx, message)

    async def _notify_failure(self, ctx: RunContext, reason: str) -> None:
        messaging = _messaging()
        retry = messaging.button(
            ctx.user_id, "🔁 Try again", "run_retry", target_id=ctx.application_id
        )
        await messaging.notify(
            ctx.user_id,
            f"⚠️ I couldn't finish the <b>{escape(ctx.company or 'job')}</b> application: "
            f"{escape(reason[:500])}",
            buttons=[[retry]],
            html=True,
            kind="run_failed",
            run_id=ctx.run_id,
        )

    def _prepare(self, ctx: RunContext) -> PreparedRun:
        with SessionLocal() as session:
            app = session.get(Application, ctx.application_id)
            job = session.get(Job, app.job_id)
            user = session.get(User, app.user_id)
            base = get_base_document(session, user.id, DocumentKind.resume)
            tailored = session.get(Document, app.resume_doc_id) if app.resume_doc_id else None
            cover = session.get(Document, app.cover_doc_id) if app.cover_doc_id else None
            if cover is None:
                cover = get_base_document(session, user.id, DocumentKind.cover_letter)

            uploads_dir = RUNS_DIR / str(ctx.run_id) / "uploads"
            uploads_dir.mkdir(parents=True, exist_ok=True)
            uploads: list[UploadFile] = []
            resume_doc = None
            if tailored and tailored.pdf_path and Path(tailored.pdf_path).is_file():
                resume_doc, source = tailored, Path(tailored.pdf_path)
            elif base and base.file_path and Path(base.file_path).is_file():
                resume_doc, source = base, Path(base.file_path)
            else:
                raise ValueError("Upload your resume as a PDF in JobBee first.")
            staged = uploads_dir / source.name
            shutil.copy2(source, staged)
            uploads.append(UploadFile("resume", staged))
            if cover is not None and cover.pdf_path and Path(cover.pdf_path).is_file():
                cover_staged = uploads_dir / Path(cover.pdf_path).name
                shutil.copy2(cover.pdf_path, cover_staged)
                uploads.append(UploadFile("cover_letter", cover_staged))

            account = accounts.get_account(session, user.id, site_host(job.url))
            dossier = build_dossier(
                user,
                resume=resume_doc,
                cover_letter=cover,
                job=job,
                uploads=uploads,
                site_account=account.status if account else "none",
                inbox=inbox_connected(session, user),
            )
            return PreparedRun(
                url=job.url,
                ats=detect_ats(job.url),
                task=build_task(job.url, dossier.render()),
                uploads_dir=uploads_dir,
                company=job.company or "the company",
                title=job.title,
            )

    async def _browser(self):
        if self._session is not None and self._session.is_cdp_connected:
            return self._session
        if self._session is not None:  # the window was closed
            with contextlib.suppress(Exception):
                await self._session.kill()
            self._parked.clear()
        self._session = new_browser_session()
        self._session_has_run = False
        await self._session.start()
        return self._session

    def _record_stats(self, run_id: int, stats: RunStats) -> None:
        self._update(
            run_id,
            pending=None,
            finish=stats.finish,
            error=stats.error,
            answers=(stats.finish or {}).get("answers"),
            iterations=stats.iterations,
            tool_calls=stats.tool_calls,
            input_tokens=stats.input_tokens,
            output_tokens=stats.output_tokens,
            cache_read_tokens=stats.cache_read_tokens,
            cache_write_tokens=stats.cache_write_tokens,
            cost_usd=stats.cost_usd,
            guard_trips=stats.guard_trips,
            uninspected_clicks=stats.uninspected_clicks,
        )
        if not (stats.finish and stats.finish["status"] == "ready_for_review"):
            self._update(run_id, status=stats.outcome, finished_at=_now())
            self._add_event(run_id, "status", {"status": stats.outcome})

    # --- parking + card 2 -----------------------------------------------------------------

    async def _park(self, ctx: RunContext, driver: Any, stats: RunStats) -> None:
        shot = RUNS_DIR / str(ctx.run_id) / "review.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        ctx.driver = driver
        ctx.reviewed_url = await driver.screenshot_png(ctx.tab_id, shot)
        ctx.network_mark = driver.network_mark()
        self._parked[ctx.run_id] = ctx
        ttl = timedelta(minutes=get_settings().submit_ttl_min)
        self._update(
            ctx.run_id,
            status="awaiting_submit",
            review_url=ctx.reviewed_url,
            screenshot_path=str(shot),
            expires_at=_now() + ttl,
        )
        self._add_event(ctx.run_id, "status", {"status": "awaiting_submit"})
        self._set_app(ctx.application_id, ApplicationStatus.awaiting_submit)
        await self._send_submit_card(ctx, stats.finish or {}, shot, ttl)
        ctx.expiry = asyncio.get_running_loop().create_task(self._expire_later(ctx, ttl))

    async def _set_aside(self, ctx: RunContext, stats: RunStats) -> None:
        """Only a human at the PC can go on: tell the user, and move on to the next run."""

        reason = (stats.finish or {}).get("summary") or "Something only you can do at your PC."
        self._set_app(ctx.application_id, ApplicationStatus.approved, f"Needs you: {reason}")
        self._score(ctx.run_id)
        messaging = _messaging()
        group = messaging.new_group()
        buttons = [
            [
                messaging.button(
                    ctx.user_id, "🖥 I'm at my PC", "run_attend", target_id=ctx.run_id, group=group
                ),
                messaging.button(
                    ctx.user_id, "Skip it", "run_dismiss", target_id=ctx.run_id, group=group
                ),
            ]
        ]
        await messaging.notify(
            ctx.user_id,
            f"🖥 Set aside <b>{escape(ctx.company)}</b> for when you're at your PC:\n"
            f"{escape(reason[:600])}\n\nThe other applications carry on.",
            buttons=buttons,
            html=True,
            kind="set_aside",
            run_id=ctx.run_id,
        )

    async def revise(self, run_id: int, changes: list[dict]) -> None:
        """Change answers on a parked form; the worker does it before its next run."""

        if not self._transition(
            run_id, "awaiting_submit", "revising", pending={"type": "revise", "changes": changes}
        ):
            raise RunStateError("This form was already handled or has expired.")
        ctx = self._parked.get(run_id)
        if ctx is None:
            self._update(run_id, status="expired", pending=None, finished_at=_now())
            raise RunStateError("The filled form isn't open any more.")
        if ctx.expiry and not ctx.expiry.done():
            ctx.expiry.cancel()
        self._add_event(run_id, "revise", {"changes": changes})
        self._revisions.append(run_id)
        self.kick()

    async def _revise(self, ctx: RunContext) -> None:
        with SessionLocal() as session:
            run = session.get(ApplyRun, ctx.run_id)
            changes = list((run.pending or {}).get("changes") or [])
            answers = list(run.answers or [])
            ats, cost = run.ats, run.cost_usd
        try:
            stats = await run_agent(
                make_client(),
                ctx.driver,
                task=build_revise_task(changes),
                ats=ats,
                config=replace(ctx.config, max_iterations=_REVISE_ITERATIONS),
                hooks=RunHooks(self, ctx),
            )
        except Exception as e:  # reported on the card; the form stays parked
            logger.exception("Revising run %s failed", ctx.run_id)
            stats = RunStats(error=str(e) or e.__class__.__name__)
        done = bool(stats.finish and stats.finish["status"] == "ready_for_review")
        shot = RUNS_DIR / str(ctx.run_id) / "review.png"
        if done:
            with contextlib.suppress(Exception):
                ctx.reviewed_url = await ctx.driver.screenshot_png(ctx.tab_id, shot)
                ctx.network_mark = ctx.driver.network_mark()
            answers = merge_answers(answers, changes)
        problem = None if done else ((stats.finish or {}).get("summary") or stats.error)
        ttl = timedelta(minutes=get_settings().submit_ttl_min)
        self._update(
            ctx.run_id,
            status="awaiting_submit",
            pending=None,
            answers=answers,
            review_url=ctx.reviewed_url,
            error=f"Couldn't change the answers: {problem}" if problem else None,
            cost_usd=cost + stats.cost_usd,
            expires_at=_now() + ttl,
        )
        self._add_event(ctx.run_id, "revised", {"changes": changes, "ok": done, "error": problem})
        with SessionLocal() as session:
            run = session.get(ApplyRun, ctx.run_id)
            finish = {**(run.finish or {}), "answers": answers}
            card_id = run.submit_card_message_id
        messaging = _messaging()
        await messaging.clear_buttons(ctx.user_id, card_id)
        if problem:
            await messaging.notify(
                ctx.user_id,
                f"I couldn't change the {ctx.company} answers: {problem}",
                run_id=ctx.run_id,
            )
        await self._send_submit_card(ctx, finish, shot, ttl)
        ctx.expiry = asyncio.get_running_loop().create_task(self._expire_later(ctx, ttl))

    @staticmethod
    def _learn(run_id: int, user_id: int) -> None:
        """Judgement calls and corrections the user approved by tapping Submit."""

        with SessionLocal() as session:
            answers = session.get(ApplyRun, run_id).answers or []
        for answer in answers:
            field_name, value = answer.get("field"), answer.get("value")
            if answer.get("source") in _LEARNED_SOURCES and field_name and value:
                remember_answer(user_id, str(field_name), str(value))

    async def _send_submit_card(
        self, ctx: RunContext, finish: dict, shot: Path, ttl: timedelta
    ) -> None:
        messaging = _messaging()
        checks = finish.get("fields_needing_review") or []
        caption = f"📝 <b>{escape(ctx.title[:90])}</b> — {escape(ctx.company[:60])}\n" + escape(
            (finish.get("summary") or "")[:400]
        )
        if checks:
            caption += "\n\n<b>Please check:</b>\n" + "\n".join(
                f"• {escape(c[:120])}" for c in checks[:5]
            )
        await messaging.notify(
            ctx.user_id, caption, photo=shot, html=True, kind="review", run_id=ctx.run_id
        )
        answers = finish.get("answers") or []
        marks = {"judgement": "🤔 ", "consent": "✍️ ", "you": "✏️ "}
        lines = [
            f"• {marks.get(a.get('source'), '')}<b>{escape(str(a.get('field', ''))[:80])}</b>: "
            f"{escape(str(a.get('value', ''))[:160])}"
            for a in answers[:40]
        ]
        if len(answers) > 40:
            lines.append(f"…and {len(answers) - 40} more.")
        group = messaging.new_group()
        buttons = [
            [
                messaging.button(
                    ctx.user_id,
                    "🚀 Submit",
                    "run_submit",
                    target_id=ctx.run_id,
                    group=group,
                    ttl=ttl,
                ),
                messaging.button(
                    ctx.user_id,
                    "✋ Don't submit",
                    "run_discard",
                    target_id=ctx.run_id,
                    group=group,
                    ttl=ttl,
                ),
            ]
        ]
        minutes = int(ttl.total_seconds() // 60)
        legend = (
            "🤔 my judgement call · ✍️ a consent I gave · ✏️ your change\n\n"
            if any(a.get("source") in marks for a in answers)
            else ""
        )
        text = (
            "<b>What I filled in</b>\n" + "\n".join(lines) + "\n\n" + legend if lines else ""
        ) + f"Submit this application? (The form stays open for {minutes} minutes.)"
        message_id = await messaging.notify(
            ctx.user_id, text, buttons=buttons, html=True, kind="submit_card", run_id=ctx.run_id
        )
        self._update(ctx.run_id, submit_card_message_id=message_id)

    async def _expire_later(self, ctx: RunContext, ttl: timedelta) -> None:
        await asyncio.sleep(ttl.total_seconds())
        if not self._transition(ctx.run_id, "awaiting_submit", "expired", finished_at=_now()):
            return
        self._parked.pop(ctx.run_id, None)
        self._free_slot()
        await self._close_driver(ctx, close_tab=True)
        self._set_app(
            ctx.application_id,
            ApplicationStatus.approved,
            "The filled form timed out before you approved it. Fill it again: JobBee reuses "
            "the account and your answers.",
        )
        self._score(ctx.run_id)
        messaging = _messaging()
        with SessionLocal() as session:
            card_id = session.get(ApplyRun, ctx.run_id).submit_card_message_id
        await messaging.clear_buttons(ctx.user_id, card_id)
        refill = messaging.button(
            ctx.user_id, "🔁 Fill it again", "run_retry", target_id=ctx.application_id
        )
        await messaging.notify(
            ctx.user_id,
            f"⏰ The {ctx.company} form timed out before you approved it, so I closed it. "
            "Filling it again needs nothing from you.",
            buttons=[[refill]],
            run_id=ctx.run_id,
        )

    def _free_slot(self) -> None:
        if self._slot_freed is not None:
            self._slot_freed.set()

    async def _close_driver(self, ctx: RunContext, *, close_tab: bool = False) -> None:
        driver, ctx.driver = ctx.driver, None
        if driver is None:
            return
        with contextlib.suppress(Exception):
            await driver.close()
        if close_tab and ctx.tab_id and self._session is not None:
            with contextlib.suppress(Exception):
                await self._close_tab(ctx.tab_id)

    async def _close_tab(self, tab_id: str) -> None:
        """Close a run's tab, or quit Chrome when it's the last one; the next run reopens it.

        Closing the last tab would quit Chrome anyway, and browser-use would then try to
        reconnect. `kill()` tells it the stop is on purpose.
        """
        async with self._browser_lock:
            session = self._session
            if session is None:
                return
            manager = session.session_manager
            pages = manager.get_all_page_targets() if manager else []
            if any(page.target_id != tab_id for page in pages):
                await session.close_page(tab_id)
                return
            self._session = None
            await session.kill()

    # --- submitting -----------------------------------------------------------------------

    async def _submit(self, ctx: RunContext) -> str:
        messaging = _messaging()
        driver = ctx.driver
        try:
            clicked, detail = await driver.click_final_submit(ctx.tab_id, ctx.reviewed_url)
        except Exception as e:
            clicked, detail = False, str(e) or e.__class__.__name__
        self._add_event(ctx.run_id, "submit_click", {"clicked": clicked, "detail": detail})

        confirmation: dict[str, Any] = {"clicked": clicked, "detail": detail}
        confirmed = False
        if clicked:
            confirmed, evidence = await self._await_confirmation(ctx)
            confirmation.update(evidence)
        posts = submit_request_rows(driver.requests_since(ctx.network_mark)) if driver else []
        confirmation["requests"] = posts[:5]

        if confirmed:
            self._update(
                ctx.run_id,
                status="submitted",
                confirmation=confirmation,
                submit_candidates=posts,
                finished_at=_now(),
            )
            self._set_app(ctx.application_id, ApplicationStatus.submitted)
            await self._close_driver(ctx, close_tab=True)
            await messaging.notify(
                ctx.user_id,
                f"✅ Applied to <b>{escape(ctx.title[:90])}</b> at {escape(ctx.company)}.",
                html=True,
                kind="submitted",
                run_id=ctx.run_id,
            )
            return "submitted"

        self._update(
            ctx.run_id,
            status="submit_unconfirmed",
            confirmation=confirmation,
            submit_candidates=posts,
        )
        await self._close_driver(ctx)  # its tab stays open for the user to check
        group = messaging.new_group()
        buttons = [
            [
                messaging.button(
                    ctx.user_id,
                    "✅ It went through",
                    "run_outcome",
                    target_id=ctx.run_id,
                    payload={"ok": True},
                    group=group,
                ),
                messaging.button(
                    ctx.user_id,
                    "❌ It didn't",
                    "run_outcome",
                    target_id=ctx.run_id,
                    payload={"ok": False},
                    group=group,
                ),
            ]
        ]
        if clicked:
            text = (
                f"I clicked Submit on the {escape(ctx.company)} form but didn't see a "
                "confirmation. Please check the tab in the JobBee Chrome window."
            )
        else:
            text = (
                f"I couldn't submit the {escape(ctx.company)} form safely "
                f"({escape(detail[:200])}). "
                "It's still open in the JobBee Chrome window: submit it there if it looks right."
            )
        await messaging.notify(
            ctx.user_id,
            text,
            buttons=buttons,
            html=True,
            kind="submit_unconfirmed",
            run_id=ctx.run_id,
        )
        return "submit_unconfirmed"

    async def _await_confirmation(self, ctx: RunContext) -> tuple[bool, dict]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _CONFIRM_WAIT_S
        url, text = ctx.reviewed_url or "", ""
        while loop.time() < deadline:
            await asyncio.sleep(1.5)
            try:
                url, text = await ctx.driver.page_text(ctx.tab_id)
            except Exception:
                continue
            match = CONFIRMATION_RE.search(text or "")
            if match:
                return True, {"url": url, "text": match.group(0)}
            if re.search(r"(thank|confirm|success|submitted|applied)", url or "", re.I):
                return True, {"url": url, "text": ""}
        return False, {"url": url, "text": ""}

    # --- scorecard ------------------------------------------------------------------------

    @staticmethod
    def _score(run_id: int) -> None:
        with SessionLocal() as session:
            run = session.get(ApplyRun, run_id)
            if run is None:
                return
            start = (
                run.created_at
                if run.created_at.tzinfo
                else run.created_at.replace(tzinfo=timezone.utc)
            )
            row = {
                "run_id": f"ui-{run.id}",
                "url": run.url,
                "ats": run.ats,
                "model": run.model,
                "effort": run.effort,
                "finish": run.finish,
                "error": run.error,
                "outcome": run.status,
                "iterations": run.iterations,
                "tool_calls": run.tool_calls,
                "cost_usd": run.cost_usd,
                "guard_trips": run.guard_trips,
                "uninspected_clicks": run.uninspected_clicks,
                "wall_s": round((_now() - start).total_seconds()),
                "submit_candidates": (run.submit_candidates or [])[:5],
                "reached_review": bool(
                    run.finish and run.finish.get("status") == "ready_for_review"
                ),
                "agent_submitted": False,
            }
        append_scorecard(row)

    # --- chat buttons ---------------------------------------------------------------------

    def register_chat_actions(self, messaging) -> None:
        router = messaging.router

        async def on_answer(ctx) -> None:
            try:
                self.answer(ctx.token.target_id, (ctx.token.payload or {}).get("value", ""))
            except RunStateError as e:
                await messaging.notify(ctx.user_id, str(e))

        async def on_submit(ctx) -> None:
            messaging.spawn(self._decide_from_chat(ctx, submit=True))

        async def on_discard(ctx) -> None:
            messaging.spawn(self._decide_from_chat(ctx, submit=False))

        async def on_outcome(ctx) -> None:
            try:
                await self.resolve_unconfirmed(
                    ctx.token.target_id, went_through=bool((ctx.token.payload or {}).get("ok"))
                )
            except RunStateError as e:
                await messaging.notify(ctx.user_id, str(e))

        async def on_retry(ctx) -> None:
            await messaging.clear_buttons(ctx.user_id, ctx.message_id)
            try:
                self.retry(ctx.token.target_id)
                await messaging.notify(ctx.user_id, "🔁 Queued it again.")
            except (RunConflictError, ValueError, LookupError) as e:
                await messaging.notify(ctx.user_id, str(e))

        async def on_attend(ctx) -> None:
            await messaging.clear_buttons(ctx.user_id, ctx.message_id)
            try:
                self.attend(ctx.token.target_id)
                await messaging.notify(ctx.user_id, "🖥 Trying it again now, with you there.")
            except (RunStateError, RunConflictError, ValueError, LookupError) as e:
                await messaging.notify(ctx.user_id, str(e))

        async def on_dismiss(ctx) -> None:
            await messaging.clear_buttons(ctx.user_id, ctx.message_id)
            try:
                self.dismiss(ctx.token.target_id)
                await messaging.notify(ctx.user_id, "OK, skipped.")
            except RunStateError as e:
                await messaging.notify(ctx.user_id, str(e))

        router.register_action("run_attend", on_attend)
        router.register_action("run_dismiss", on_dismiss)
        router.register_action("run_answer", on_answer)
        router.register_action("run_submit", on_submit)
        router.register_action("run_discard", on_discard)
        router.register_action("run_outcome", on_outcome)
        router.register_action("run_retry", on_retry)

    async def _decide_from_chat(self, ctx, *, submit: bool) -> None:
        try:
            await self.decide(ctx.token.target_id, submit=submit)
        except RunStateError as e:
            await _messaging().notify(ctx.user_id, str(e))

    # --- queries --------------------------------------------------------------------------

    @staticmethod
    def latest_run_ids(application_ids: list[int]) -> dict[int, int]:
        if not application_ids:
            return {}
        with SessionLocal() as session:
            rows = session.execute(
                select(ApplyRun.application_id, ApplyRun.id)
                .where(ApplyRun.application_id.in_(application_ids))
                .order_by(ApplyRun.id)
            ).all()
        return {app_id: run_id for app_id, run_id in rows}


run_manager = RunManager()
