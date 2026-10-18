"""Unit tests for deterministic job-alert email parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.email_parsers import (
    LinkedInEmailParser,
    extract_sender_address,
    get_parser_for_sender,
    parse_email_body,
)
from app.agents.email_parsers.base import (
    canonical_linkedin_job_url,
    extract_linkedin_job_id,
    unwrap_tracking_url,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "emails"


def _read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extract_sender_address() -> None:
    assert extract_sender_address("LinkedIn Job Alerts <jobs-listings@linkedin.com>") == (
        "jobs-listings@linkedin.com"
    )
    assert extract_sender_address("jobs-listings@linkedin.com") == "jobs-listings@linkedin.com"


def test_unwrap_click_linkedin_tracking_url() -> None:
    wrapped = (
        "https://click.linkedin.com/redirect?"
        "url=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F4200000001"
    )
    assert unwrap_tracking_url(wrapped) == "https://www.linkedin.com/jobs/view/4200000001"


def test_extract_linkedin_job_id_from_comm_url() -> None:
    url = "https://www.linkedin.com/comm/jobs/view/4100000001?tracking=abc"
    assert extract_linkedin_job_id(url) == "4100000001"
    assert canonical_linkedin_job_url("4100000001") == (
        "https://www.linkedin.com/jobs/view/4100000001"
    )


def test_linkedin_parser_matches_known_senders() -> None:
    parser = LinkedInEmailParser()
    assert parser.matches_sender("jobs-listings@linkedin.com")
    assert parser.matches_sender("LinkedIn <jobalerts-noreply@linkedin.com>")
    assert not parser.matches_sender("messages-noreply@linkedin.com")
    assert not parser.matches_sender("alert@indeed.com")


def test_get_parser_for_sender_returns_linkedin() -> None:
    parser = get_parser_for_sender("jobs-listings@linkedin.com")
    assert parser is not None
    assert parser.alert_source == "email_linkedin"


def test_parse_linkedin_digest_fixture() -> None:
    body = _read_fixture("linkedin_digest.html")
    alerts = parse_email_body(
        sender="jobs-listings@linkedin.com",
        subject="New jobs matching Python Developer",
        body=body,
    )

    assert len(alerts) == 3
    urls = {alert.url for alert in alerts}
    assert urls == {
        "https://www.linkedin.com/jobs/view/4100000001",
        "https://www.linkedin.com/jobs/view/4100000002",
        "https://www.linkedin.com/jobs/view/4100000003",
    }

    by_title = {alert.title: alert for alert in alerts}
    assert by_title["Senior Python Developer"].company == "Redacted Corp"
    assert by_title["Senior Python Developer"].location == "Remote"
    assert by_title["Backend Engineer"].company == "Example Industries"
    assert by_title["Backend Engineer"].location == "Austin, TX"
    assert all(alert.alert_source == "email_linkedin" for alert in alerts)


def test_parse_linkedin_single_tracking_fixture() -> None:
    body = _read_fixture("linkedin_single_tracking.html")
    alerts = parse_email_body(
        sender="jobs-listings@linkedin.com",
        subject="Machine Learning Engineer at Northwind Analytics",
        body=body,
    )

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.url == "https://www.linkedin.com/jobs/view/4200000001"
    assert alert.title == "Machine Learning Engineer"
    assert alert.company == "Northwind Analytics"
    assert alert.location == "Boston, MA (Hybrid)"
    assert alert.snippet is not None
    assert "ML pipelines" in alert.snippet


def test_parse_linkedin_plain_text_fixture() -> None:
    body = _read_fixture("linkedin_plain.txt")
    alerts = parse_email_body(
        sender="jobalerts-noreply@linkedin.com",
        subject="Your job alert for python developer",
        body=body,
    )

    assert len(alerts) == 2
    titles = {alert.title for alert in alerts}
    assert titles == {"Senior Python Developer", "Backend Engineer"}
    assert all(alert.url.startswith("https://www.linkedin.com/jobs/view/") for alert in alerts)


def test_parse_email_body_unknown_sender_returns_empty() -> None:
    assert (
        parse_email_body(
            sender="newsletter@example.com",
            subject="Weekly news",
            body="<a href='https://www.linkedin.com/jobs/view/1'>Job</a>",
        )
        == []
    )


def test_company_from_subject_and_plain_text() -> None:
    body = (
        "Agentic AI Engineer D3 Security · Greater Vancouver, BC (On-site) "
        '<a href="https://www.linkedin.com/jobs/view/999">Agentic AI Engineer</a>'
    )
    alerts = parse_email_body(
        sender="jobs-listings@linkedin.com",
        subject="Software Engineer - Data and AI Platforms at lululemon",
        body=body,
    )
    assert len(alerts) == 1
    assert alerts[0].company in {"D3 Security", "lululemon"}
    assert alerts[0].location and "Greater Vancouver" in alerts[0].location
    body = """
    <a href="https://www.linkedin.com/jobs/view/5000000001">Platform Engineer</a>
    <p>Contoso · Remote</p>
    <a href="https://click.linkedin.com/redirect?url=https%3A%2F%2Fwww.linkedin.com%2Fjobs%2Fview%2F5000000001">
      View job
    </a>
    """
    alerts = parse_email_body(
        sender="jobs-listings@linkedin.com",
        subject="Platform Engineer",
        body=body,
    )
    assert len(alerts) == 1
    assert alerts[0].title == "Platform Engineer"
