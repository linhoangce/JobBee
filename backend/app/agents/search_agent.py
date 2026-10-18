"""Search agent: pull listings from configured board APIs, persist, and score.

Primary ingestion is inbox sync (LinkedIn job-alert emails). This agent remains
for optional board sources and re-scoring jobs returned by an explicit search.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from ..models import Job, User
from ..services.job_ingest import ensure_application, score_jobs_batch, upsert_job
from .sources import JobSourceError, get_sources

logger = logging.getLogger(__name__)

# Polite delay between external API calls (seconds).
_SOURCE_DELAY_S = 1.5
_DEFAULT_LIMIT_PER_SOURCE = 25
@dataclass(slots=True)
class SearchResult:
    sources_queried: list[str] = field(default_factory=list)
    jobs_found: int = 0
    jobs_upserted: int = 0
    jobs_scored: int = 0
    warnings: list[str] = field(default_factory=list)


def _search_query(user: User) -> str:
    if user.target_roles:
        return user.target_roles[0]
    if user.skills:
        return user.skills[0]
    if user.headline:
        return user.headline.split("·")[0].strip()
    return "software engineer"


async def run_search(
    session: Session,
    user: User,
    *,
    sources: list[str] | None = None,
    limit_per_source: int = _DEFAULT_LIMIT_PER_SOURCE,
    score_jobs: bool = True,
) -> SearchResult:
    result = SearchResult()
    query = _search_query(user)

    try:
        source_map = get_sources(sources)
    except JobSourceError as e:
        result.warnings.append(str(e))
        return result

    if not source_map:
        result.warnings.append(
            "No job board sources configured. Connect Gmail in Settings and sync "
            "LinkedIn job-alert emails from the Jobs page."
        )
        return result

    all_listings = []
    for idx, (name, fetcher) in enumerate(source_map.items()):
        if idx > 0:
            await asyncio.sleep(_SOURCE_DELAY_S)
        result.sources_queried.append(name)
        try:
            listings = await fetcher(query=query, limit=limit_per_source)
            all_listings.extend(listings)
        except JobSourceError as e:
            msg = f"{name}: {e}"
            result.warnings.append(msg)
            logger.warning(msg)

    result.jobs_found = len(all_listings)

    touched_jobs: list[Job] = []
    for listing in all_listings:
        job, created = upsert_job(session, listing)
        ensure_application(session, user, job)
        touched_jobs.append(job)
        if created:
            result.jobs_upserted += 1

    session.commit()

    if not score_jobs or not touched_jobs:
        return result

    jobs_scored, score_warnings = await score_jobs_batch(session, user, touched_jobs)
    result.jobs_scored = jobs_scored
    result.warnings.extend(score_warnings)
    session.commit()
    return result
