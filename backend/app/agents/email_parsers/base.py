"""Shared types and utilities for job-alert email parsers."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Protocol
from urllib.parse import parse_qs, unquote, urlparse

LINKEDIN_JOB_URL_RE = re.compile(
    r"https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(\d+)",
    re.IGNORECASE,
)
SENDER_EMAIL_RE = re.compile(r"[\w.+-]+@[\w.-]+\.\w+")
SKIP_LINK_TEXT = frozenset(
    {
        "",
        "view job",
        "apply",
        "see job",
        "see all",
        "manage alerts",
        "unsubscribe",
        "learn more",
    }
)


@dataclass(slots=True)
class ParsedJobAlert:
    """Structured job listing extracted from an alert email."""

    url: str
    title: str
    company: str | None = None
    location: str | None = None
    snippet: str | None = None
    alert_source: str = ""


class EmailParser(Protocol):
    alert_source: str

    def matches_sender(self, sender: str) -> bool:
        """Return True when this parser handles the given From header."""

    def parse(self, *, subject: str, body: str) -> list[ParsedJobAlert]:
        """Extract zero or more job alerts from the message body."""


def extract_sender_address(sender: str) -> str:
    """Pull the bare email address from a RFC5322 From header."""

    match = SENDER_EMAIL_RE.search(sender or "")
    return match.group(0).lower() if match else (sender or "").strip().lower()


def unwrap_tracking_url(url: str) -> str:
    """Resolve click/redirect wrappers to the destination URL."""

    cleaned = (url or "").strip()
    if not cleaned:
        return cleaned

    parsed = urlparse(cleaned)
    host = parsed.netloc.lower()

    if host.endswith("click.linkedin.com") or "/redir/redirect" in parsed.path:
        wrapped = parse_qs(parsed.query).get("url", [None])[0]
        if wrapped:
            return unquote(wrapped)

    return cleaned


def canonical_linkedin_job_url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}"


def extract_linkedin_job_id(url: str) -> str | None:
    resolved = unwrap_tracking_url(url)
    match = LINKEDIN_JOB_URL_RE.search(resolved)
    return match.group(1) if match else None


@dataclass(slots=True)
class _HtmlLink:
    href: str
    text: str


class _LinkExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[_HtmlLink] = []
        self._current_href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        for name, value in attrs:
            if name.lower() == "href" and value:
                self._current_href = value
                self._text_parts = []
                break

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        text = " ".join("".join(self._text_parts).split())
        self.links.append(_HtmlLink(href=self._current_href, text=text))
        self._current_href = None
        self._text_parts = []


def extract_html_links(html: str) -> list[tuple[str, str]]:
    parser = _LinkExtractor()
    try:
        parser.feed(html)
        parser.close()
    except HTMLParser.HTMLParseError:
        return []
    return [(link.href, link.text) for link in parser.links]


def clean_text(text: str) -> str:
    """Decode HTML entities and normalize whitespace."""

    return " ".join(html.unescape(text).split())


def strip_html_to_text(html_str: str) -> str:
    """Best-effort plain text for regex fallbacks."""

    without_tags = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_str, flags=re.I | re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_tags)
    return clean_text(unquote(without_tags))


def split_company_location(value: str) -> tuple[str | None, str | None]:
    """Parse 'Acme Corp · San Francisco, CA' style fragments."""

    cleaned = clean_text(value.replace("&middot;", "·"))
    if not cleaned:
        return None, None

    for sep in (" · ", " | ", " - ", " — "):
        if sep in cleaned:
            company, location = cleaned.split(sep, 1)
            return company.strip() or None, location.strip() or None

    return cleaned, None


def is_meaningful_link_text(text: str) -> bool:
    normalized = " ".join(text.split()).lower()
    return bool(normalized) and normalized not in SKIP_LINK_TEXT


_PARSERS: list[EmailParser] = []


def register_parser(parser: EmailParser) -> None:
    _PARSERS.append(parser)


def get_parser_for_sender(sender: str) -> EmailParser | None:
    for parser in _PARSERS:
        if parser.matches_sender(sender):
            return parser
    return None


def parse_email_body(*, sender: str, subject: str, body: str) -> list[ParsedJobAlert]:
    parser = get_parser_for_sender(sender)
    if parser is None:
        return []
    return parser.parse(subject=subject, body=body)
