"""What a source hands to discovery: a posting URL plus whatever the listing already says."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class Candidate:
    url: str
    source: str  # watchlist | web_search | email
    title: str | None = None
    company: str | None = None
    location: str | None = None
    snippet: str | None = None
    posted_at: datetime | None = None


class SourceError(RuntimeError):
    """A source couldn't be read this time (shown as a warning, never fatal)."""
