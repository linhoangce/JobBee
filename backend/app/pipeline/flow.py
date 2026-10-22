"""Per-job flow: filter → screen → tailor CV → approval card (tap 1) → Apply/Skip.

The same methods serve the web UI (HTTP routes) and the chat (Telegram buttons):
`screen` and `prepare_card` do the work, `approve` and `skip` are the user's
decision on a card. Chat notifications are a silent no-op when no chat is
linked. Slow work runs in background tasks; `stages` says what is in flight so
the UI can show it, and `notices` keeps recent problems for the UI's inbox.
"""

from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from sqlalchemy import select, update

from ..config import BACKEND_DIR
from ..db import SessionLocal
from ..documents.render_pdf import (
    cover_letter_html,
    file_stem,
    get_style,
    html_to_pdf,
    resume_html,
)
from ..llm.base import LLMError
from ..llm.factory import get_llm_provider
from ..messaging.base import Button
from ..messaging.bot import ActionContext
from ..messaging.service import MessagingService, messaging
from ..models import Application, ApplicationStatus, Document, DocumentKind, Job, User
from ..services.documents import delete_file_if_present, get_base_document, page_budget
from .ingest import IngestError, IngestResult, ingest_url
from .prefs import load_preferences, save_preferences
from .screen import hard_filter_reason, screen_job
from .tailor import StructuredCv, cv_to_text, tailor_cv

logger = logging.getLogger(__name__)

DOCS_DIR = BACKEND_DIR / "data" / "documents"

SKIP_REASONS = {
    "not_interested": "Not interested",
    "level": "Wrong level",
    "location": "Location",
    "salary": "Salary",
    "company": "This company",
}

STAGE_SCREENING = "screening"
STAGE_TAILORING = "tailoring"
_MAX_NOTICES = 20


class FlowError(RuntimeError):
    """A user-facing reason an action can't happen (already handled, missing resume…)."""


@dataclass
class Notice:
    id: int
    user_id: int
    text: str
    level: str = "error"  # error | info
    application_id: int | None = None
    retry: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


_notice_ids = itertools.count(1)


def _short(text: str | None, limit: int) -> str:
    text = " ".join((text or "").split())
    return escape(text if len(text) <= limit else text[: limit - 1] + "…")


def job_label(job: Job) -> str:
    return f"{job.title} at {job.company}" if job.company else job.title


def approval_caption(job: Job, app: Application) -> str:
    screen = app.screen or {}
    tailoring = app.tailoring or {}
    where = " · ".join(p for p in (job.location, job.work_mode) if p)
    lines = [
        f"<b>{_short(job.title, 90)}</b>",
        f"🏢 {_short(job.company or 'Unknown company', 60)}",
    ]
    if where:
        lines.append(f"📍 {_short(where, 80)}")
    if job.salary_text:
        lines.append(f"💰 {_short(job.salary_text, 60)}")
    if screen.get("score") is not None:
        lines.append(f"🎯 Match {int(screen['score'])}/100 — {_short(screen.get('summary'), 180)}")
    if screen.get("decision") == "drop":
        lines.append("⚠️ Probably not a fit, but you sent it, so here it is.")
    if screen.get("gaps"):
        lines.append("Gaps: " + "; ".join(_short(g, 70) for g in screen["gaps"][:2]))
    if tailoring.get("changes"):
        lines.append("✍️ CV: " + "; ".join(_short(c, 80) for c in tailoring["changes"][:2]))
    if tailoring.get("removed_claims"):
        lines.append(f"🧹 Removed {len(tailoring['removed_claims'])} claim(s) I couldn't verify.")
    lines.append("\nApply? I'll fill the form and show it to you before submitting.")
    return "\n".join(lines)


class JobFlow:
    def __init__(self, messaging: MessagingService) -> None:
        self.messaging = messaging
        self.stages: dict[int, str] = {}
        self.notices: list[Notice] = []

    # --- registration --------------------------------------------------------------

    def register(self) -> None:
        router = self.messaging.router
        router.set_url_handler(self.on_link)
        router.register_action("apply", self.on_apply)
        router.register_action("skip", self.on_skip)
        router.register_action("skip_reason", self.on_skip_reason)

    # --- notices (shown in the UI inbox) ----------------------------------------------

    def add_notice(
        self,
        user_id: int,
        text: str,
        *,
        level: str = "error",
        application_id: int | None = None,
        retry: bool = False,
    ) -> Notice:
        if application_id is not None:
            self.dismiss_for(application_id)
        notice = Notice(
            id=next(_notice_ids),
            user_id=user_id,
            text=text,
            level=level,
            application_id=application_id,
            retry=retry,
        )
        self.notices = [notice, *self.notices][:_MAX_NOTICES]
        return notice

    def dismiss_notice(self, notice_id: int) -> None:
        self.notices = [n for n in self.notices if n.id != notice_id]

    def dismiss_for(self, application_id: int) -> None:
        self.notices = [n for n in self.notices if n.application_id != application_id]

    # --- links (chat and UI) -----------------------------------------------------------

    async def on_link(self, user_id: int, url: str) -> None:
        await self.messaging.notify(user_id, "🔎 Reading that posting…", kind="progress")
        self.messaging.spawn(self.handle_link(user_id, url))

    async def handle_link(self, user_id: int, url: str) -> None:
        """Chat entry point: ingest the link, then screen and prepare it."""

        try:
            result = await self.add_link(user_id, url, source="chat")
        except IngestError as e:
            await self.messaging.notify(user_id, f"🤔 {e}", kind="error")
            return
        if not self.needs_processing(result):
            label = result.status.value.replace("_", " ")
            await self.messaging.notify(
                user_id, f"You already have this one ({label}).", kind="info"
            )
            return
        await self.process(result.application_id, user_requested=True)

    async def add_link(self, user_id: int, url: str, *, source: str = "link") -> IngestResult:
        return await ingest_url(user_id, url, source=source)

    @staticmethod
    def needs_processing(result: IngestResult) -> bool:
        return not result.duplicate or result.status in (
            ApplicationStatus.matched,
            ApplicationStatus.filtered_out,
        )

    def start(self, application_id: int, *, user_requested: bool) -> None:
        """Process in the background (the HTTP request returns right away)."""

        self.stages.setdefault(application_id, STAGE_SCREENING)
        self.messaging.spawn(self._process_started(application_id, user_requested))

    async def _process_started(self, application_id: int, user_requested: bool) -> None:
        self.stages.pop(application_id, None)
        await self.process(application_id, user_requested=user_requested)

    def start_prepare(self, application_id: int) -> None:
        """Tailor a shortlisted job in the background."""

        self.stages.setdefault(application_id, STAGE_TAILORING)
        self.messaging.spawn(self._prepare_started(application_id))

    async def _prepare_started(self, application_id: int) -> None:
        self.stages.pop(application_id, None)
        try:
            await self.prepare_card(application_id)
        except LLMError as e:
            await self._failed(application_id, e, notify_chat=False)

    # --- the per-job pipeline ------------------------------------------------------------

    async def process(self, application_id: int, *, user_requested: bool) -> str:
        """Filter, screen, tailor and send the approval card. Returns the outcome."""

        if application_id in self.stages:
            return "busy"
        self.stages[application_id] = STAGE_SCREENING
        try:
            outcome = await self._screen(application_id, user_requested=user_requested)
            if outcome != "kept":
                return outcome
            await self._prepare_card(application_id)
            return "card_sent"
        except LLMError as e:
            await self._failed(application_id, e, notify_chat=user_requested)
            return "error"
        finally:
            self.stages.pop(application_id, None)

    async def screen(self, application_id: int, *, user_requested: bool = False) -> str:
        """Hard filters + Haiku screening: "filtered" | "screened_out" | "kept" | "busy"."""

        if application_id in self.stages:
            return "busy"
        self.stages[application_id] = STAGE_SCREENING
        try:
            return await self._screen(application_id, user_requested=user_requested)
        finally:
            self.stages.pop(application_id, None)

    async def prepare_card(self, application_id: int) -> str:
        """Tailor the CV and put the card in front of the user: "card_sent" | "busy"."""

        if application_id in self.stages:
            return "busy"
        self.stages[application_id] = STAGE_TAILORING
        try:
            await self._prepare_card(application_id)
            return "card_sent"
        finally:
            self.stages.pop(application_id, None)

    async def _failed(self, application_id: int, error: Exception, *, notify_chat: bool) -> None:
        logger.warning("Processing application %s failed: %s", application_id, error)
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            if app is None:
                return
            label = job_label(session.get(Job, app.job_id))
            user_id = app.user_id
        self.add_notice(
            user_id,
            f"Couldn't prepare {label}: {error}",
            application_id=application_id,
            retry=True,
        )
        if notify_chat:
            await self.messaging.notify(
                user_id, f"⚠️ I couldn't finish that one: {error}", kind="error"
            )

    async def _screen(self, application_id: int, *, user_requested: bool) -> str:
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            user = session.get(User, app.user_id)
            base = get_base_document(session, user.id, DocumentKind.resume)
            if base is None or not (base.content or "").strip():
                raise LLMError("Upload your resume in JobBee first.")
            base_text = base.content
            prefs = load_preferences(user)
            reason = hard_filter_reason(user, job, prefs, user_requested=user_requested)
            if reason:
                app.status = ApplicationStatus.filtered_out
                app.stage_reason = reason
                session.commit()
                if user_requested:
                    self.add_notice(
                        user.id,
                        f"Not preparing {job_label(job)}: {reason}",
                        level="info",
                        application_id=application_id,
                    )
                    await self.messaging.notify(
                        user.id, f"Not preparing this one: {reason}", kind="filtered"
                    )
                return "filtered"
            session.expunge_all()

        verdict = await screen_job(get_llm_provider(), user, job, base_text)
        keep = user_requested or (
            verdict.decision == "keep" and verdict.score >= prefs.min_match_score
        )
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job_row = session.get(Job, app.job_id)
            app.screen = verdict.as_dict()
            job_row.match_score = float(verdict.score)
            job_row.match_reason = verdict.summary
            job_row.match_source = "llm"
            if keep:
                app.status = ApplicationStatus.matched
                app.stage_reason = None
            else:
                app.status = ApplicationStatus.filtered_out
                app.stage_reason = f"Screened out ({verdict.score}/100): {verdict.summary}"
            session.commit()
        return "kept" if keep else "screened_out"

    async def _prepare_card(self, application_id: int) -> None:
        self.stages[application_id] = STAGE_TAILORING
        await self.prepare_documents(application_id)
        await self.send_approval_card(application_id)
        self.dismiss_for(application_id)

    async def prepare_documents(self, application_id: int) -> None:
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            user = session.get(User, app.user_id)
            base = get_base_document(session, user.id, DocumentKind.resume)
            if base is None or not (base.content or "").strip():
                raise LLMError("Upload your resume in JobBee first.")
            base_text = base.content
            pages = page_budget(base)
            style = (app.tailoring or {}).get("style") or load_preferences(user).document_style
            session.expunge_all()

        cv = await tailor_cv(get_llm_provider(), user, job, base_text, pages)
        await self._write_documents(
            application_id,
            cv.resume,
            cv.cover_letter,
            style,
            {
                "changes": cv.changes,
                "removed_claims": cv.removed_claims[:10],
                "prepared_at": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def restyle(self, application_id: int, style: str) -> None:
        """Render the same tailored CV and letter again in another style (no Claude call)."""

        with SessionLocal() as session:
            tailoring = dict(session.get(Application, application_id).tailoring or {})
        if not tailoring.get("cv"):
            raise FlowError("This CV was made before styles existed.")
        kept = {
            k: tailoring[k] for k in ("changes", "removed_claims", "prepared_at") if k in tailoring
        }
        await self._write_documents(
            application_id,
            StructuredCv.model_validate(tailoring["cv"]),
            tailoring.get("cover_letter") or "",
            style,
            kept,
        )

    async def _write_documents(
        self,
        application_id: int,
        resume: StructuredCv,
        letter: str,
        style: str,
        tailoring: dict,
    ) -> None:
        style = get_style(style).id
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            user = session.get(User, app.user_id)
            pages = page_budget(get_base_document(session, user.id, DocumentKind.resume))
            session.expunge_all()

        stem = file_stem(resume.name or user.name or "Candidate")
        out_dir = DOCS_DIR / str(application_id)
        resume_pdf = out_dir / f"{stem}_Resume.pdf"
        await html_to_pdf(lambda fit: resume_html(resume, style, fit), resume_pdf, pages)
        cover_pdf = None
        if letter:
            cover_pdf = out_dir / f"{stem}_Cover_Letter.pdf"
            await html_to_pdf(
                lambda fit: cover_letter_html(resume, letter, job.company, job.title, style, fit),
                cover_pdf,
            )

        with SessionLocal() as session:
            app = session.get(Application, application_id)
            old = session.execute(
                select(Document).where(
                    Document.application_id == application_id, Document.is_base.is_(False)
                )
            ).scalars()
            keep_paths = {str(resume_pdf), str(cover_pdf)}
            for doc in old:
                if doc.pdf_path not in keep_paths:
                    delete_file_if_present(doc.pdf_path)
                session.delete(doc)
            title = job.title[:80]
            resume_doc = Document(
                user_id=app.user_id,
                kind=DocumentKind.resume,
                label=f"Tailored resume — {title}",
                content=cv_to_text(resume),
                pdf_path=str(resume_pdf),
                mime_type="application/pdf",
                byte_size=resume_pdf.stat().st_size,
                is_base=False,
                application_id=application_id,
            )
            session.add(resume_doc)
            cover_doc = None
            if cover_pdf is not None:
                cover_doc = Document(
                    user_id=app.user_id,
                    kind=DocumentKind.cover_letter,
                    label=f"Cover letter — {title}",
                    content=letter,
                    pdf_path=str(cover_pdf),
                    mime_type="application/pdf",
                    byte_size=cover_pdf.stat().st_size,
                    is_base=False,
                    application_id=application_id,
                )
                session.add(cover_doc)
            session.flush()
            app.resume_doc_id = resume_doc.id
            app.cover_doc_id = cover_doc.id if cover_doc else None
            app.tailoring = {
                **tailoring,
                "resume_pdf": str(resume_pdf),
                "cover_pdf": str(cover_pdf) if cover_pdf else None,
                "cv": resume.model_dump(mode="json"),
                "cover_letter": letter,
                "style": style,
            }
            session.commit()

    async def send_approval_card(self, application_id: int) -> None:
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            caption = approval_caption(job, app)
            resume_pdf = (app.tailoring or {}).get("resume_pdf")
            user_id, url = app.user_id, job.url
            app.status = ApplicationStatus.awaiting_approval
            session.commit()
        group = self.messaging.new_group()
        buttons = [
            [
                self.messaging.button(
                    user_id, "✅ Apply", "apply", target_id=application_id, group=group
                ),
                self.messaging.button(
                    user_id, "⏭ Skip", "skip", target_id=application_id, group=group
                ),
            ],
            [Button("🔗 Posting", url=url)],
        ]
        message_id = await self.messaging.notify(
            user_id,
            caption,
            buttons=buttons,
            document=Path(resume_pdf) if resume_pdf else None,
            html=True,
            kind="approval_card",
            application_id=application_id,
        )
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            app.card_message_id = message_id
            session.commit()

    # --- the user's decision on a card (UI and chat) ---------------------------------------

    @staticmethod
    def _transition(application_id: int, user_id: int, src, dst) -> bool:
        with SessionLocal() as session:
            result = session.execute(
                update(Application)
                .where(
                    Application.id == application_id,
                    Application.user_id == user_id,
                    Application.status == src,
                )
                .values(status=dst)
            )
            session.commit()
            return result.rowcount == 1

    @staticmethod
    def _card(application_id: int) -> tuple[str | None, str]:
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            return app.card_message_id, job.company or "the company"

    async def approve(self, application_id: int, user_id: int) -> int:
        """Apply: queue the agent. Returns the run id."""

        from ..apply.runs import RunConflictError, run_manager

        if not self._transition(
            application_id,
            user_id,
            ApplicationStatus.awaiting_approval,
            ApplicationStatus.approved,
        ):
            raise FlowError("That one was already handled.")
        card_id, company = self._card(application_id)
        await self.messaging.clear_buttons(user_id, card_id)
        try:
            run_id = run_manager.enqueue(application_id)
        except (RunConflictError, ValueError, LookupError) as e:
            raise FlowError(str(e)) from e
        await self.messaging.notify(
            user_id,
            f"👍 On it. I'll fill in the {company} application and show it to you before "
            "anything is submitted.",
            kind="approved",
            application_id=application_id,
        )
        return run_id

    async def skip(self, application_id: int, user_id: int, reason: str | None = None) -> str:
        """Skip a card, optionally saying why. Returns a short confirmation."""

        if not self._transition(
            application_id,
            user_id,
            ApplicationStatus.awaiting_approval,
            ApplicationStatus.rejected_by_user,
        ):
            raise FlowError("That one was already handled.")
        card_id, _ = self._card(application_id)
        await self.messaging.clear_buttons(user_id, card_id)
        self.dismiss_for(application_id)
        if reason:
            return self.record_skip_reason(application_id, user_id, reason)
        with SessionLocal() as session:
            session.get(Application, application_id).stage_reason = "Skipped"
            session.commit()
        return "Skipped."

    @staticmethod
    def record_skip_reason(application_id: int, user_id: int, reason: str) -> str:
        reply = "Thanks, noted."
        with SessionLocal() as session:
            app = session.get(Application, application_id)
            job = session.get(Job, app.job_id)
            user = session.get(User, user_id)
            app.stage_reason = f"Skipped: {SKIP_REASONS.get(reason, reason)}"
            prefs = load_preferences(user)
            prefs.skip_reasons[reason] = prefs.skip_reasons.get(reason, 0) + 1
            if reason == "company" and job.company and not prefs.excludes(job.company):
                prefs.excluded_companies.append(job.company)
                reply = f"Got it — I won't send {job.company} jobs again."
            save_preferences(user, prefs)
            session.commit()
        return reply

    # --- chat buttons ----------------------------------------------------------------------

    async def on_apply(self, ctx: ActionContext) -> None:
        await self.messaging.clear_buttons(ctx.user_id, ctx.message_id)
        try:
            await self.approve(ctx.token.target_id, ctx.user_id)
        except FlowError as e:
            await self.messaging.notify(ctx.user_id, str(e))

    async def on_skip(self, ctx: ActionContext) -> None:
        app_id = ctx.token.target_id
        await self.messaging.clear_buttons(ctx.user_id, ctx.message_id)
        try:
            await self.skip(app_id, ctx.user_id)
        except FlowError as e:
            await self.messaging.notify(ctx.user_id, str(e))
            return
        group = self.messaging.new_group()
        keys = list(SKIP_REASONS)
        buttons = [
            [
                self.messaging.button(
                    ctx.user_id,
                    SKIP_REASONS[k],
                    "skip_reason",
                    target_id=app_id,
                    payload={"reason": k},
                    group=group,
                )
                for k in keys[i : i + 3]
            ]
            for i in range(0, len(keys), 3)
        ]
        await self.messaging.notify(
            ctx.user_id,
            "Skipped. Why? (It helps me pick better ones.)",
            buttons=buttons,
            kind="skip",
            application_id=app_id,
        )

    async def on_skip_reason(self, ctx: ActionContext) -> None:
        reason = (ctx.token.payload or {}).get("reason", "not_interested")
        await self.messaging.clear_buttons(ctx.user_id, ctx.message_id)
        reply = self.record_skip_reason(ctx.token.target_id, ctx.user_id, reason)
        await self.messaging.notify(ctx.user_id, reply, kind="info")


job_flow = JobFlow(messaging)
