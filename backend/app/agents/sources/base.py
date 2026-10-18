"""Shared types for job listing sources."""

from __future__ import annotations

from dataclasses import dataclass


class JobSourceError(RuntimeError):
    """Raised when a source cannot return listings."""


@dataclass(slots=True)
class RawJobListing:
    """Vendor-neutral job row before we persist it."""

    source: str
    external_id: str
    url: str
    title: str
    company: str | None = None
    location: str | None = None
    description: str | None = None
    tags: list[str] | None = None
