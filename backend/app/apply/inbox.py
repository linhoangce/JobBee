"""Find the email a job site just sent: a verification link, a reset link or a one-time code.

Read from the user's connected inbox (Gmail read-only, or IMAP). Extraction is
plain code, no model: only mail from the site's own domain (or its ATS's) counts,
only links back to those domains are used, and the email body never reaches
the agent: it gets the link opened in its tab, or the code.
"""

from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parseaddr
from urllib.parse import urlsplit

from ..agents.email_parsers.base import extract_html_links, strip_html_to_text
from ..db import SessionLocal
from ..pipeline.sources.email_alerts import disconnect_if_dead, inbox_client
from ..services.email_client import EmailClientError, RawEmailMessage
from ..services.email_sync import get_or_create_email_sync_state

PURPOSES = ("verify_email", "reset_password", "sign_in_code")
WAIT_S = 180.0
POLL_S = 10.0

# Where each ATS family sends mail from, keyed by the domain its job sites live on.
_ATS_SENDERS = {
    "myworkdayjobs.com": ("myworkday.com", "workday.com"),
    "myworkdaysite.com": ("myworkday.com", "workday.com"),
    "icims.com": (),
    "greenhouse.io": (),
    "lever.co": (),
    "ashbyhq.com": (),
    "smartrecruiters.com": (),
    "successfactors.com": ("sap.com",),
    "successfactors.eu": ("sap.com",),
    "taleo.net": ("oracle.com",),
    "oraclecloud.com": ("oracle.com",),
}
_COUNTRY_SECOND_LEVEL = {"co", "com", "ac", "org", "gov", "net", "edu"}

_LINK_WORDS = {
    "verify_email": re.compile(r"verif|activat|confirm|validat", re.I),
    "reset_password": re.compile(r"reset|password|recover|forgot", re.I),
}
_CODE_PATTERNS = (
    re.compile(r"(?:code|passcode|pin|one[- ]time password|otp)\D{0,40}?\b(\d{4,8})\b", re.I),
    re.compile(r"\b(\d{4,8})\b\D{0,30}(?:is your|verification|security code)", re.I),
)


class InboxUnavailable(Exception):
    """No usable inbox; the message says why, for the agent."""


@dataclass
class InboxFind:
    message_id: str
    sender: str
    subject: str
    link: str | None = None
    code: str | None = None


def site_domain(host: str) -> str:
    """The registrable domain, e.g. acme.wd5.myworkdayjobs.com → myworkdayjobs.com."""

    labels = host.lower().strip(".").split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _COUNTRY_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def trusted_domains(host: str) -> list[str]:
    domain = site_domain(host)
    return sorted({domain, *_ATS_SENDERS.get(domain, ())})


def _on(domain_or_host: str, domains: list[str]) -> bool:
    name = domain_or_host.lower().rstrip(".")
    return any(name == d or name.endswith("." + d) for d in domains)


def _link(message: RawEmailMessage, domains: list[str], words: re.Pattern) -> str | None:
    candidates = extract_html_links(message.body) if "<" in message.body else []
    candidates += [(url, "") for url in re.findall(r"https://[^\s<>\"')\]]+", message.body)]
    for href, text in candidates:
        url = html.unescape(href).strip()
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or not _on(parts.hostname, domains):
            continue
        if words.search(f"{parts.path}?{parts.query} {text}"):
            return url
    return None


def _code(message: RawEmailMessage) -> str | None:
    text = f"{message.subject} {strip_html_to_text(message.body)}"
    for pattern in _CODE_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def extract(message: RawEmailMessage, host: str, purpose: str) -> InboxFind | None:
    """What the agent may use from this message for `purpose`, if it's from the site."""

    domains = trusted_domains(host)
    sender = parseaddr(message.sender)[1]
    if "@" not in sender or not _on(sender.rsplit("@", 1)[1], domains):
        return None
    found = InboxFind(message.provider_message_id, sender, message.subject)
    words = _LINK_WORDS.get(purpose)
    if words is not None:
        found.link = _link(message, domains, words)
    if found.link is None and purpose != "reset_password":
        found.code = _code(message)
    return found if found.link or found.code else None


async def wait_for_email(
    user_id: int,
    host: str,
    purpose: str,
    since: datetime,
    *,
    skip: set[str] | frozenset[str] = frozenset(),
    timeout: float = WAIT_S,
    interval: float = POLL_S,
) -> InboxFind | None:
    """Poll the inbox until the site's email arrives; None if it didn't in time."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    senders = trusted_domains(host)
    while True:
        with SessionLocal() as session:
            state = get_or_create_email_sync_state(session, user_id)
            try:
                client, state = inbox_client(session, user_id)
                if client is None:
                    raise InboxUnavailable(
                        "No inbox is connected to JobBee, so it can't read the site's email."
                    )
                messages = await asyncio.to_thread(
                    client.search_recent, senders=senders, since=since
                )
            except EmailClientError as e:
                raise InboxUnavailable(
                    disconnect_if_dead(session, state, e) or f"Reading the inbox failed: {e}"
                ) from None
        for message in messages:
            if message.provider_message_id in skip:
                continue
            found = extract(message, host, purpose)
            if found is not None:
                return found
        if loop.time() + interval > deadline:
            return None
        await asyncio.sleep(interval)
