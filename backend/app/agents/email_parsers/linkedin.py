"""LinkedIn job-alert email parser (deterministic, no LLM)."""

from __future__ import annotations

import html
import re

from .base import (
    EmailParser,
    ParsedJobAlert,
    canonical_linkedin_job_url,
    clean_text,
    extract_html_links,
    extract_linkedin_job_id,
    extract_sender_address,
    is_meaningful_link_text,
    register_parser,
    split_company_location,
    strip_html_to_text,
    unwrap_tracking_url,
)

LINKEDIN_JOB_ALERT_SENDERS = frozenset(
    {
        "jobs-listings@linkedin.com",
        "jobalerts-noreply@linkedin.com",
        "jobs@linkedin.com",
    }
)

COMPANY_LOCATION_AFTER_LINK_RE = re.compile(
    r"(?P<title>[^<\n]+?)\s*(?:</a>|</strong>|\n)\s*"
    r"(?P<meta>[^<\n]{2,120}?)\s*(?:<|$|\n)",
    re.IGNORECASE,
)
PLAIN_JOB_BLOCK_RE = re.compile(
    r"(?P<title>[A-Za-z0-9][^\n]{4,120})\s*\n\s*"
    r"(?P<meta>[^\n]{2,120})\s*\n\s*"
    r"(?P<url>https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/\d+[^\s]*)",
    re.IGNORECASE,
)


class LinkedInEmailParser:
    alert_source = "email_linkedin"

    def matches_sender(self, sender: str) -> bool:
        return extract_sender_address(sender) in LINKEDIN_JOB_ALERT_SENDERS

    def parse(self, *, subject: str, body: str) -> list[ParsedJobAlert]:
        if not body.strip():
            return []

        if "<" in body and ">" in body:
            alerts = self._parse_html(body, subject=subject)
            if alerts:
                return alerts

        return self._parse_plain(body, subject=subject)

    def _parse_html(self, html_str: str, *, subject: str) -> list[ParsedJobAlert]:
        by_id: dict[str, ParsedJobAlert] = {}
        plain = strip_html_to_text(html_str)

        for href, text in extract_html_links(html_str):
            job_id = extract_linkedin_job_id(href)
            if not job_id:
                continue

            title = text if is_meaningful_link_text(text) else None
            company: str | None = None
            location: str | None = None

            if title is None:
                title, company, location = self._metadata_near_url(html_str, href)
            else:
                company, location = self._metadata_near_link(html_str, href, title)

            if not title:
                title = self._title_from_subject_or_fallback(plain, job_id, subject=subject)

            alert = ParsedJobAlert(
                url=canonical_linkedin_job_url(job_id),
                title=title,
                company=company,
                location=location,
                snippet=self._snippet_for_job(plain, title, company),
                alert_source=self.alert_source,
            )
            self._enrich_company_location(alert, plain, subject)
            by_id[job_id] = alert

        for job_id, title, company, location in self._regex_blocks(html_str):
            if job_id in by_id:
                existing = by_id[job_id]
                if not existing.company and company:
                    existing.company = company
                if not existing.location and location:
                    existing.location = location
                continue
            alert = ParsedJobAlert(
                url=canonical_linkedin_job_url(job_id),
                title=title,
                company=company,
                location=location,
                snippet=self._snippet_for_job(plain, title, company),
                alert_source=self.alert_source,
            )
            self._enrich_company_location(alert, plain, subject)
            by_id[job_id] = alert

        return list(by_id.values())

    def _parse_plain(self, body: str, *, subject: str) -> list[ParsedJobAlert]:
        by_id: dict[str, ParsedJobAlert] = {}

        for match in PLAIN_JOB_BLOCK_RE.finditer(body):
            url = unwrap_tracking_url(match.group("url"))
            job_id = extract_linkedin_job_id(url)
            if not job_id:
                continue
            title = match.group("title").strip()
            company, location = split_company_location(match.group("meta").strip())
            by_id[job_id] = ParsedJobAlert(
                url=canonical_linkedin_job_url(job_id),
                title=title,
                company=company,
                location=location,
                snippet=self._snippet_for_job(body, title, company),
                alert_source=self.alert_source,
            )
            self._enrich_company_location(by_id[job_id], body, subject)

        if by_id:
            return list(by_id.values())

        for raw_url in re.findall(
            r"https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/\d+[^\s<>\"']*",
            body,
            flags=re.IGNORECASE,
        ):
            url = unwrap_tracking_url(raw_url)
            job_id = extract_linkedin_job_id(url)
            if not job_id or job_id in by_id:
                continue
            title = self._title_from_subject_or_fallback(body, job_id, subject=subject)
            alert = ParsedJobAlert(
                url=canonical_linkedin_job_url(job_id),
                title=title,
                snippet=self._snippet_for_job(body, title, None),
                alert_source=self.alert_source,
            )
            self._enrich_company_location(alert, body, subject)
            by_id[job_id] = alert

        return list(by_id.values())

    def _enrich_company_location(self, alert: ParsedJobAlert, plain: str, subject: str) -> None:
        if not alert.company:
            alert.company = _company_from_subject(subject)
        company, location = _company_location_from_plain(plain, alert.title)
        if company and not alert.company:
            alert.company = company
        if location and not alert.location:
            alert.location = location
        if alert.snippet:
            alert.snippet = clean_text(alert.snippet)

    def _metadata_near_link(
        self,
        html: str,
        href: str,
        title: str,
    ) -> tuple[str | None, str | None]:
        resolved = unwrap_tracking_url(href)
        idx = html.lower().find(resolved.lower())
        if idx < 0:
            idx = html.lower().find(href.lower())
        if idx < 0:
            return None, None

        window = html[idx : idx + 400]
        meta_match = re.search(
            r"</a>\s*(?:<[^>]+>\s*)*(?P<meta>[^<]{2,120})",
            window,
            flags=re.IGNORECASE,
        )
        if not meta_match:
            return None, None
        meta = meta_match.group("meta").strip()
        if title.lower() in meta.lower():
            return None, None
        return split_company_location(meta)

    def _metadata_near_url(
        self,
        html: str,
        href: str,
    ) -> tuple[str | None, str | None, str | None]:
        resolved = unwrap_tracking_url(href)
        idx = html.lower().find(resolved.lower())
        if idx < 0:
            idx = html.lower().find(href.lower())
        if idx < 0:
            return None, None, None

        start = max(0, idx - 250)
        window = html[start:idx + 120]
        title_match = re.search(
            r">(?P<title>[^<]{4,120})</a>\s*(?:<[^>]+>\s*)*(?P<meta>[^<]{2,120})",
            window,
            flags=re.IGNORECASE,
        )
        if not title_match:
            return None, None, None
        title = title_match.group("title").strip()
        company, location = split_company_location(title_match.group("meta").strip())
        return title, company, location

    def _regex_blocks(self, html: str) -> list[tuple[str, str, str | None, str | None]]:
        rows: list[tuple[str, str, str | None, str | None]] = []
        for match in COMPANY_LOCATION_AFTER_LINK_RE.finditer(html):
            block = match.group(0)
            url_match = re.search(
                r"https?://(?:[\w-]+\.)*linkedin\.com/(?:comm/)?jobs/view/(\d+)",
                block,
                flags=re.IGNORECASE,
            )
            if not url_match:
                continue
            job_id = url_match.group(1)
            title = match.group("title").strip()
            company, location = split_company_location(match.group("meta").strip())
            if title:
                rows.append((job_id, title, company, location))
        return rows

    def _title_from_subject_or_fallback(
        self,
        body: str,
        job_id: str,
        *,
        subject: str = "",
    ) -> str:
        if subject:
            cleaned = re.sub(r"^(new jobs matching|job alert:?)\s*", "", subject, flags=re.I).strip()
            if cleaned and cleaned.lower() not in {"linkedin", "your job alert"}:
                return cleaned
        return f"LinkedIn job {job_id}"

    def _snippet_for_job(
        self,
        text: str,
        title: str,
        company: str | None,
    ) -> str | None:
        needle = title
        idx = text.lower().find(needle.lower())
        if idx < 0:
            if company:
                idx = text.lower().find(company.lower())
            if idx < 0:
                return None
        snippet = clean_text(text[idx : idx + 200].strip())
        return snippet or None


def _company_from_subject(subject: str) -> str | None:
    match = re.search(r"\s+at\s+([^|:]+?)\s*$", (subject or "").strip(), re.IGNORECASE)
    return match.group(1).strip() if match else None


def _company_location_from_plain(plain: str, title: str) -> tuple[str | None, str | None]:
    """Parse 'Title Company · Location' blocks common in LinkedIn digest emails."""

    cleaned = html.unescape(plain)
    pattern = re.compile(
        re.escape(title) + r"\s+(?P<company>[^·|]+?)\s*[·|]\s*(?P<location>.{2,100}?)"
        r"(?:\s+\d+\s+school|\s+Actively recruiting|\s+[A-Z][a-z]+ (?:Engineer|Developer|Manager)\b|\s*$)",
        re.IGNORECASE,
    )
    match = pattern.search(cleaned)
    if not match:
        return None, None
    location = match.group("location").strip()
    location = re.sub(r"\s*\([^)]*\)\s*$", "", location).strip()
    location = re.split(
        r"\s+(?=[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Engineer|Developer|Manager|Analyst)\b)",
        location,
    )[0].strip()
    return match.group("company").strip(), location or None


register_parser(LinkedInEmailParser())
