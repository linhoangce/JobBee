"""Find jobs: gather postings from every source, screen them, send the best as cards.

One search at a time. A search:
  1. collects candidates from the company watchlist, Claude web search and alert emails;
  2. drops known postings, excluded companies and (for whole job boards) unrelated titles;
  3. fetches and stores up to `max_screens_per_run` postings, then filters and screens them;
  4. prepares tailored-CV cards for the best kept jobs, up to `jobs_per_day` per day.
Kept jobs beyond today's quota stay shortlisted; `more()` prepares the next ones.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from ..apply.agent import make_client
from ..db import SessionLocal
from ..llm.base import LLMError
from ..llm.usage import BudgetExceededError, spend_since
from ..messaging.service import messaging
from ..models import Application, ApplicationStatus, DiscoveryRun, Job, User, WatchlistCompany
from ..services.secrets import claude_api_key
from .flow import JobFlow, job_flow
from .ingest import store_details
from .jd import (
    JobDetails,
    JobFetchError,
    PostingClosedError,
    canonicalize_url,
    fetch_job_details,
)
from .prefs import load_preferences
from .sources.ats_boards import list_board, role_relevance
from .sources.base import Candidate, SourceError
from .sources.email_alerts import fetch_alerts
from .sources.web_search import search_postings

logger = logging.getLogger(__name__)

_FETCH_CONCURRENCY = 4
_SCREEN_CONCURRENCY = 3
_RECENT_UNSCREENED = timedelta(days=14)
MIN_DESCRIPTION_CHARS = 300
STAGES = {
    "sources": "Looking for new postings",
    "reading": "Reading postings",
    "screening": "Checking which ones fit",
    "preparing": "Tailoring your CV for the best ones",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def local_day_start(now: datetime | None = None) -> datetime:
    local = (now or _now()).astimezone()
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


def prepared_today(user_id: int, now: datetime | None = None) -> int:
    """Cards prepared since local midnight (links the user sent included)."""

    start = local_day_start(now)
    with SessionLocal() as session:
        rows = session.execute(
            select(Application.tailoring).where(
                Application.user_id == user_id,
                Application.tailoring.is_not(None),
                # Cards withdrawn because the posting closed don't use up the day's quota.
                Application.status != ApplicationStatus.filtered_out,
            )
        ).scalars()
        count = 0
        for tailoring in rows:
            stamp = (tailoring or {}).get("prepared_at")
            try:
                if stamp and datetime.fromisoformat(stamp) >= start:
                    count += 1
            except ValueError:
                continue
        return count


def shortlist(user_id: int, min_score: int) -> list[int]:
    """Kept-but-not-sent applications, best first."""

    with SessionLocal() as session:
        rows = session.execute(
            select(Application.id, Application.screen)
            .where(
                Application.user_id == user_id,
                Application.status == ApplicationStatus.matched,
                Application.screen.is_not(None),
            )
            .order_by(Application.id.desc())
        ).all()
    kept = [
        (app_id, int(screen.get("score", 0)))
        for app_id, screen in rows
        if screen and screen.get("decision") == "keep" and screen.get("score", 0) >= min_score
    ]
    return [app_id for app_id, _ in sorted(kept, key=lambda r: r[1], reverse=True)]


async def recheck_open_postings(user_id: int) -> list[int]:
    """Withdraw cards and shortlisted jobs whose posting has since closed. Returns their ids."""

    with SessionLocal() as session:
        rows = session.execute(
            select(Application.id, Application.status, Application.screen, Job.url)
            .join(Job, Job.id == Application.job_id)
            .where(
                Application.user_id == user_id,
                Application.status.in_(
                    (ApplicationStatus.awaiting_approval, ApplicationStatus.matched)
                ),
            )
        ).all()
    open_ids = [
        (app_id, url)
        for app_id, status, screen, url in rows
        if status == ApplicationStatus.awaiting_approval or (screen or {}).get("decision") == "keep"
    ]
    gate = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def closed(url: str) -> bool:
        async with gate:
            try:
                await fetch_job_details(url)
            except PostingClosedError:
                return True
            except JobFetchError:
                return False  # unreachable right now; don't withdraw on a hiccup
        return False

    results = await asyncio.gather(*(closed(url) for _, url in open_ids))
    gone = [app_id for (app_id, _), is_closed in zip(open_ids, results, strict=True) if is_closed]
    if gone:
        with SessionLocal() as session:
            for app_id in gone:
                app = session.get(Application, app_id)
                app.status = ApplicationStatus.filtered_out
                app.stage_reason = "The posting was taken down."
            session.commit()
    return gone


@dataclass
class SearchSummary:
    found: int = 0
    new: int = 0
    screened: int = 0
    kept: int = 0
    sent: int = 0
    sources: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class Discovery:
    def __init__(self, flow: JobFlow) -> None:
        self.flow = flow
        self._task: asyncio.Task | None = None
        self.stage: str | None = None
        self.started_at: datetime | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, user_id: int, trigger: str = "manual") -> bool:
        if self.running:
            return False
        self._task = asyncio.get_running_loop().create_task(
            self._guarded(self.run(user_id, trigger))
        )
        return True

    def start_more(self, user_id: int, count: int = 3) -> bool:
        if self.running:
            return False
        self._task = asyncio.get_running_loop().create_task(
            self._guarded(self.more(user_id, count))
        )
        return True

    async def stop(self) -> None:
        if self.running:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _guarded(self, coro) -> None:
        try:
            await coro
        except Exception:
            logger.exception("Job search failed")
        finally:
            self.stage = None
            self.started_at = None

    # --- a full search -------------------------------------------------------------------

    async def run(self, user_id: int, trigger: str = "manual") -> int:
        self.started_at = _now()
        self.stage = "sources"
        with SessionLocal() as session:
            row = DiscoveryRun(user_id=user_id, trigger=trigger, status="running", sources={})
            session.add(row)
            session.commit()
            run_id = row.id
        summary = SearchSummary()
        status, error = "done", None
        try:
            with SessionLocal() as session:
                user = session.get(User, user_id)
                prefs = load_preferences(user)
                session.expunge(user)
            await recheck_open_postings(user_id)
            candidates = await self.gather(user, prefs, summary)
            summary.found = len(candidates)
            self.stage = "reading"
            app_ids = await self.ingest(user, candidates, summary)
            app_ids = self._with_recent_unscreened(user_id, app_ids)[: prefs.max_screens_per_run]
            self.stage = "screening"
            await self.screen_all(app_ids, summary)
            self.stage = "preparing"
            quota = max(prefs.jobs_per_day - prepared_today(user_id), 0)
            summary.sent = await self.send_best(user_id, prefs.min_match_score, quota)
        except BudgetExceededError as e:
            status, error = "stopped", str(e)
        except LLMError as e:
            status, error = "failed", str(e)
        except asyncio.CancelledError:
            status, error = "stopped", "Stopped."
            raise
        finally:
            if summary.warnings and error is None:
                error = " · ".join(summary.warnings[:3])
                if status == "done" and summary.found == 0:
                    status = "failed"  # every source failed; nothing was searched
            self._finish(run_id, summary, status, error)
        await self._digest(user_id, summary, status, error)
        return run_id

    async def more(self, user_id: int, count: int) -> int:
        """Prepare cards for the next best shortlisted jobs (no new search)."""

        self.started_at = _now()
        self.stage = "preparing"
        with SessionLocal() as session:
            row = DiscoveryRun(user_id=user_id, trigger="more", status="running", sources={})
            session.add(row)
            session.commit()
            run_id = row.id
            prefs = load_preferences(session.get(User, user_id))
        summary = SearchSummary()
        status, error = "done", None
        try:
            summary.sent = await self.send_best(user_id, prefs.min_match_score, count)
        except BudgetExceededError as e:
            status, error = "stopped", str(e)
        except LLMError as e:
            status, error = "failed", str(e)
        finally:
            self._finish(run_id, summary, status, error)
        return run_id

    def _finish(self, run_id: int, summary: SearchSummary, status: str, error: str | None) -> None:
        with SessionLocal() as session:
            row = session.get(DiscoveryRun, run_id)
            row.status = status
            row.error = error
            row.found, row.new = summary.found, summary.new
            row.screened, row.kept, row.sent = summary.screened, summary.kept, summary.sent
            row.sources = summary.sources
            started = (
                row.started_at
                if row.started_at.tzinfo
                else row.started_at.replace(tzinfo=timezone.utc)
            )
            row.cost_usd = round(spend_since(started), 4)
            row.finished_at = _now()
            session.commit()

    async def _digest(
        self, user_id: int, summary: SearchSummary, status: str, error: str | None
    ) -> None:
        if status != "done":
            text = f"⚠️ Job search stopped: {error}"
        elif summary.sent:
            text = (
                f"🐝 Found {summary.found} · kept {summary.kept} · sent you {summary.sent} new "
                f"match{'es' if summary.sent != 1 else ''}."
            )
        else:
            text = (
                f"🐝 Found {summary.found} · kept {summary.kept}. Nothing new worth sending today."
            )
        await messaging.notify(user_id, text, kind="digest")

    # --- steps ------------------------------------------------------------------------------

    async def gather(self, user: User, prefs, summary: SearchSummary) -> list[Candidate]:
        """Candidates from every enabled source; a failing source is a warning, not an error."""

        candidates: list[Candidate] = []

        with SessionLocal() as session:
            boards = (
                session.execute(select(WatchlistCompany).where(WatchlistCompany.user_id == user.id))
                .scalars()
                .all()
            )
            session.expunge_all()
        board_hits: list[tuple[int, Candidate]] = []
        for board in boards:
            try:
                listed = await list_board(board.ats, board.slug, board.name)
                error = None
            except SourceError as e:
                listed, error = [], str(e)
                summary.warnings.append(str(e))
            with SessionLocal() as session:
                row = session.get(WatchlistCompany, board.id)
                row.last_polled_at, row.last_error = _now(), error
                session.commit()
            for c in listed:
                score = role_relevance(c.title, user.target_roles or [])
                if score:
                    board_hits.append((score, c))
        board_hits.sort(key=lambda hit: hit[0], reverse=True)
        candidates += [c for _, c in board_hits]
        if boards:
            summary.sources["watchlist"] = len(board_hits)

        if prefs.web_search_enabled and claude_api_key():
            domains = sorted(
                {
                    host
                    for b in boards
                    if b.careers_url and (host := (urlsplit(b.careers_url).hostname or ""))
                }
            )
            try:
                found = await search_postings(
                    make_client(),
                    user,
                    excluded=prefs.excluded_companies,
                    max_age_days=prefs.max_job_age_days,
                    extra_domains=domains,
                )
                candidates += found
                summary.sources["web_search"] = len(found)
            except BudgetExceededError:
                raise
            except LLMError as e:
                summary.warnings.append(f"Web search: {e}")

        try:
            with SessionLocal() as session:
                found = await fetch_alerts(session, session.get(User, user.id))
            candidates += found
            if found:
                summary.sources["email"] = len(found)
        except SourceError as e:
            summary.warnings.append(str(e))

        return self._dedupe(candidates, prefs)

    @staticmethod
    def _dedupe(candidates: list[Candidate], prefs) -> list[Candidate]:
        seen: set[str] = set()
        with SessionLocal() as session:
            known = set(
                session.execute(select(Job.canonical_url).where(Job.canonical_url.is_not(None)))
                .scalars()
                .all()
            )
        out = []
        for c in candidates:
            key = canonicalize_url(c.url)
            if key in seen or key in known or prefs.excludes(c.company):
                continue
            seen.add(key)
            out.append(c)
        return out

    async def ingest(
        self, user: User, candidates: list[Candidate], summary: SearchSummary
    ) -> list[int]:
        """Fetch each posting (bounded) and store it; returns new applications to screen."""

        prefs_limit = load_preferences(user).max_screens_per_run
        todo = candidates[: prefs_limit * 2]  # some fetches fail or turn out to be duplicates
        gate = asyncio.Semaphore(_FETCH_CONCURRENCY)

        async def fetch(c: Candidate) -> JobDetails | None:
            """The posting as the job board shows it now, or None if it isn't a live posting.

            Search results and listings can be stale, so a job is only kept when its page
            was read. The one exception is a LinkedIn alert email behind a login wall:
            the email itself is the evidence, so its snippet stands in for the page.
            """

            async with gate:
                try:
                    details = await fetch_job_details(c.url)
                except PostingClosedError:
                    return None
                except JobFetchError:
                    details = None
            readable = (
                details is not None
                and not details.login_wall
                and len(details.description or "") >= MIN_DESCRIPTION_CHARS
            )
            if not readable:
                if c.source != "email" or not c.title:
                    return None
                details = JobDetails(url=c.url, canonical_url=canonicalize_url(c.url), ats="")
            details.title = details.title or c.title
            details.company = details.company or c.company
            details.location = details.location or c.location
            details.description = details.description or c.snippet
            details.posted_at = details.posted_at or c.posted_at
            return details if details.title else None

        fetched = await asyncio.gather(*(fetch(c) for c in todo))
        app_ids: list[int] = []
        with SessionLocal() as session:
            db_user = session.get(User, user.id)
            for c, details in zip(todo, fetched):
                if details is None:
                    continue
                result = store_details(session, db_user, details, source=c.source)
                if not result.duplicate:
                    summary.new += 1
                if result.status == ApplicationStatus.matched:
                    app_ids.append(result.application_id)
        return list(dict.fromkeys(app_ids))

    @staticmethod
    def _with_recent_unscreened(user_id: int, app_ids: list[int]) -> list[int]:
        """Also screen leftovers (links that failed, email syncs) from the last two weeks."""

        since = _now() - _RECENT_UNSCREENED
        with SessionLocal() as session:
            rows = session.execute(
                select(Application.id).where(
                    Application.user_id == user_id,
                    Application.status == ApplicationStatus.matched,
                    Application.screen.is_(None),
                    Application.created_at >= since,
                )
            ).scalars()
            leftovers = [i for i in rows if i not in app_ids]
        return app_ids + leftovers

    async def screen_all(self, app_ids: list[int], summary: SearchSummary) -> None:
        gate = asyncio.Semaphore(_SCREEN_CONCURRENCY)
        budget_error: list[BudgetExceededError] = []

        async def one(app_id: int) -> None:
            if budget_error:
                return
            async with gate:
                if budget_error:
                    return
                try:
                    outcome = await self.flow.screen(app_id)
                except BudgetExceededError as e:
                    budget_error.append(e)
                    return
                except LLMError as e:
                    logger.warning("Screening application %s failed: %s", app_id, e)
                    return
            summary.screened += 1
            if outcome == "kept":
                summary.kept += 1

        await asyncio.gather(*(one(i) for i in app_ids))
        if budget_error:
            raise budget_error[0]

    async def send_best(self, user_id: int, min_score: int, count: int) -> int:
        sent = 0
        for app_id in shortlist(user_id, min_score):
            if sent >= count:
                break
            try:
                if await self.flow.prepare_card(app_id) == "card_sent":
                    sent += 1
            except BudgetExceededError:
                raise
            except LLMError as e:
                logger.warning("Preparing application %s failed: %s", app_id, e)
                await self.flow._failed(app_id, e, notify_chat=False)
        return sent


discovery = Discovery(job_flow)
