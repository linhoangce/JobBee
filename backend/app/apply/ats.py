"""Recognise the applicant tracking system behind a job URL."""

from __future__ import annotations

from urllib.parse import urlsplit

_HOST_SUFFIXES: tuple[tuple[str, str], ...] = (
    ("myworkdayjobs.com", "workday"),
    ("myworkdaysite.com", "workday"),
    ("workday.com", "workday"),
    ("greenhouse.io", "greenhouse"),
    ("lever.co", "lever"),
    ("ashbyhq.com", "ashby"),
    ("icims.com", "icims"),
    ("linkedin.com", "linkedin_easy_apply"),
)

KNOWN_ATS = {name for _, name in _HOST_SUFFIXES} | {"generic"}


def detect_ats(url: str | None) -> str:
    host = (urlsplit(url or "").hostname or "").lower()
    for suffix, name in _HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            return name
    return "generic"
