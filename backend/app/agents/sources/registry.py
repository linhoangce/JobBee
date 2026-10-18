"""Registry of supported job sources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from .base import JobSourceError, RawJobListing

SourceFetcher = Callable[..., Awaitable[list[RawJobListing]]]

# External job-board APIs (none enabled — listings come from inbox sync).
AVAILABLE_SOURCES: dict[str, SourceFetcher] = {}

# Inbox-only sources synced via POST /api/email/sync.
EMAIL_SOURCES: frozenset[str] = frozenset({"email_linkedin"})

# Sources we deliberately do NOT ship: linkedin, indeed (ToS / bot detection risk).
BLOCKED_SOURCES: frozenset[str] = frozenset({"linkedin", "indeed"})

# Retired board APIs — kept so old clients get a clear error instead of "unknown".
RETIRED_SOURCES: frozenset[str] = frozenset({"remoteok", "remotive"})


def get_sources(names: list[str] | None = None) -> dict[str, SourceFetcher]:
    if not names:
        return dict(AVAILABLE_SOURCES)

    selected: dict[str, SourceFetcher] = {}
    for name in names:
        key = name.strip().lower()
        if key in BLOCKED_SOURCES:
            raise JobSourceError(
                f"Source {key!r} is not supported. LinkedIn/Indeed automation violates "
                "platform ToS and risks account bans. Connect Gmail and sync job-alert emails instead."
            )
        if key in RETIRED_SOURCES:
            raise JobSourceError(
                f"Source {key!r} was removed. Connect Gmail in Settings and sync LinkedIn "
                "job-alert emails from the Jobs page."
            )
        if key not in AVAILABLE_SOURCES:
            available = sorted(EMAIL_SOURCES)
            raise JobSourceError(
                f"Unknown source {key!r}. Available: {', '.join(available) or 'email_linkedin (via inbox sync)'}"
            )
        selected[key] = AVAILABLE_SOURCES[key]
    return selected
