"""Parse inbox messages into structured job alerts (a source for discovery)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from ..agents.email_parsers import parse_email_body
from ..agents.email_parsers.base import extract_linkedin_job_id, extract_sender_address
from ..services.email_client import RawEmailMessage

JOB_ALERT_SENDERS = frozenset(
    {
        "jobs-listings@linkedin.com",
        "jobalerts-noreply@linkedin.com",
        "jobs@linkedin.com",
        "alert@indeed.com",
    }
)

SKIP_SUBJECT_RE = re.compile(
    r"messaged you|waiting for your response|survey|customer service|chat session|"
    r"invitation to connect|accepted your|viewed your profile|liked your",
    re.IGNORECASE,
)

JOB_SUBJECT_HINT_RE = re.compile(
    r"jobs matching|new jobs|job alert|recommended jobs|job recommendations",
    re.IGNORECASE,
)

MAX_WARNINGS = 5


@dataclass(slots=True)
class SyncedJobAlert:
    title: str
    company: str | None
    location: str | None
    url: str
    snippet: str | None
    alert_source: str
    email_subject: str
    email_sender: str


@dataclass(slots=True)
class EmailSyncResult:
    messages_scanned: int = 0
    jobs_found: int = 0
    jobs: list[SyncedJobAlert] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def external_id_from_alert(alert: SyncedJobAlert) -> str:
    if alert.alert_source == "email_linkedin":
        job_id = extract_linkedin_job_id(alert.url)
        if job_id:
            return job_id
    return hashlib.sha256(alert.url.encode()).hexdigest()[:16]


def is_job_alert_message(message: RawEmailMessage) -> bool:
    """Skip LinkedIn DMs, surveys, and other non-job mail before parsing."""

    sender = extract_sender_address(message.sender)
    if sender in JOB_ALERT_SENDERS:
        return not SKIP_SUBJECT_RE.search(message.subject or "")

    if sender.endswith("@indeed.com") and "alert" in sender:
        return True

    subject = message.subject or ""
    if JOB_SUBJECT_HINT_RE.search(subject) and not SKIP_SUBJECT_RE.search(subject):
        body = (message.body or message.snippet or "").lower()
        return "jobs/view" in body or "indeed.com/viewjob" in body

    return False


def parse_inbox_messages(messages: list[RawEmailMessage]) -> EmailSyncResult:
    """Turn fetched inbox rows into deduplicated job alerts."""

    result = EmailSyncResult()
    seen_urls: set[str] = set()

    for message in messages:
        if not is_job_alert_message(message):
            continue

        result.messages_scanned += 1
        body = (message.body or message.snippet or "").strip()
        if not body:
            if len(result.warnings) < MAX_WARNINGS:
                result.warnings.append(f"Empty job alert email: {message.subject[:80]}")
            continue

        alerts = parse_email_body(
            sender=message.sender,
            subject=message.subject,
            body=body,
        )
        if not alerts:
            if len(result.warnings) < MAX_WARNINGS:
                result.warnings.append(
                    f"Could not parse jobs from: {message.subject[:80] or '(no subject)'}"
                )
            continue

        for alert in alerts:
            if alert.url in seen_urls:
                continue
            seen_urls.add(alert.url)
            result.jobs.append(
                SyncedJobAlert(
                    title=alert.title,
                    company=alert.company,
                    location=alert.location,
                    url=alert.url,
                    snippet=alert.snippet,
                    alert_source=alert.alert_source,
                    email_subject=message.subject,
                    email_sender=message.sender,
                )
            )

    result.jobs_found = len(result.jobs)
    return result
