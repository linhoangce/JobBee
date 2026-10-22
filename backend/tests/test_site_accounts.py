"""Job-site accounts: passwords, permissions, the verification-email reader and the API."""

from __future__ import annotations

import string
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import httpx
import pytest
from anthropic.tools import ToolError
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.api.profile import get_or_create_local_user
from app.apply import inbox
from app.apply.accounts import account_password, credentials, generate_password
from app.apply.dossier import _permissions
from app.apply.guards import (
    PASSWORD_FIELD_MESSAGE,
    sensitive_field_message,
)
from app.db import SessionLocal
from app.main import app
from app.models import SiteAccount
from app.pipeline.prefs import load_preferences, save_preferences
from app.services.email_client import GmailClient, RawEmailMessage

HOST = "acme.wd5.myworkdayjobs.com"
SINCE = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def user_id() -> int:
    with SessionLocal() as session:
        session.execute(delete(SiteAccount))
        user = get_or_create_local_user(session)
        user.email = "lin@example.com"
        prefs = load_preferences(user)
        prefs.agent_accounts = True
        save_preferences(user, prefs)
        session.commit()
        return user.id


def test_generated_passwords_meet_every_rule() -> None:
    for _ in range(50):
        password = generate_password()
        assert len(password) == 20
        assert any(c in string.ascii_uppercase for c in password)
        assert any(c in string.ascii_lowercase for c in password)
        assert any(c in string.digits for c in password)
        assert any(c in "!@#$%&*" for c in password)
    assert generate_password() != generate_password()


def test_credentials_create_sign_in_and_reset(user_id) -> None:
    with pytest.raises(ToolError, match="no saved account"):
        credentials(user_id, HOST, "sign_in")
    login, created, kind = credentials(user_id, HOST, "create")
    assert (login, kind) == ("lin@example.com", "account_created")
    assert account_password(user_id, HOST) == created
    assert credentials(user_id, HOST.upper(), "sign_in") == (login, created, "account_signed_in")
    _, reset, kind = credentials(user_id, HOST, "set_new_password")
    assert kind == "password_reset" and reset != created
    assert account_password(user_id, HOST) == reset


def test_credentials_respect_the_permission(user_id) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        prefs = load_preferences(user)
        prefs.agent_accounts = False
        save_preferences(user, prefs)
        session.commit()
    with pytest.raises(ToolError, match="hasn't allowed"):
        credentials(user_id, HOST, "create")
    assert account_password(user_id, HOST) is None


def test_a_refused_password_is_never_tried_again(user_id) -> None:
    credentials(user_id, HOST, "create")
    assert credentials(user_id, HOST, "rejected") == ("lin@example.com", "", "account_rejected")
    with pytest.raises(ToolError, match="Forgot password"):
        credentials(user_id, HOST, "sign_in")
    _, reset, _ = credentials(user_id, HOST, "set_new_password")
    assert credentials(user_id, HOST, "sign_in")[1] == reset


def test_dossier_tells_the_agent_about_the_account_and_inbox(user_id) -> None:
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        refused = "\n".join(_permissions(user, "rejected", False))
        fresh = "\n".join(_permissions(user, "none", True))
    assert "Forgot password" in refused and "NOT connected" in refused
    assert "no account" in fresh and "password reset): connected" in fresh


def test_accounts_api_lists_reveals_and_forgets(user_id) -> None:
    _, password, _ = credentials(user_id, HOST, "create")
    with TestClient(app) as client:
        rows = client.get("/api/site-accounts").json()
        assert [(r["host"], r["login"], r["status"]) for r in rows] == [
            (HOST, "lin@example.com", "created")
        ]
        assert password not in str(rows)
        account_id = rows[0]["id"]
        assert client.post(f"/api/site-accounts/{account_id}/reveal").json() == {
            "password": password
        }
        assert client.delete(f"/api/site-accounts/{account_id}").status_code == 204
        assert client.get("/api/site-accounts").json() == []
        assert client.post(f"/api/site-accounts/{account_id}/reveal").status_code == 404
    assert account_password(user_id, HOST) is None


@pytest.mark.parametrize(
    ("field", "blocked"),
    [
        ({"type": "password", "name": "Password"}, PASSWORD_FIELD_MESSAGE),
        ({"type": "text", "name": "Verify New Password"}, PASSWORD_FIELD_MESSAGE),
        ({"type": "text", "name": "Social Insurance Number (SIN)"}, "social insurance number"),
        ({"type": "text", "name": "Bank account number"}, "bank"),
        ({"type": "text", "name": "", "autocomplete": "cc-number"}, "card details"),
        ({"type": "text", "name": "First Name | firstName"}, None),
        ({"type": "email", "name": "Email Address"}, None),
        (None, None),
    ],
)
def test_sensitive_fields(field, blocked) -> None:
    message = sensitive_field_message(field)
    if blocked is None:
        assert message is None
    else:
        assert message is not None and blocked.lower() in message.lower()


# --- the verification-email reader -----------------------------------------------------------

WORKDAY_VERIFY = RawEmailMessage(
    provider_message_id="m1",
    subject="Verify your candidate account",
    sender="Acme Careers <acme@myworkday.com>",
    received_at=SINCE,
    snippet="",
    body=(
        "<p>Please confirm your email.</p>"
        "<a href='https://evil.example.com/verify?t=1'>Not this one</a>"
        "<a href='https://acme.wd5.myworkdayjobs.com/en-US/External/activate?token=abc&amp;x=1'>"
        "Verify Account</a>"
    ),
)
CODE_MAIL = RawEmailMessage(
    provider_message_id="m2",
    subject="Your sign-in code",
    sender="no-reply@myworkday.com",
    received_at=SINCE,
    snippet="",
    body="<p>Your verification code is <b>482913</b>. It expires in 10 minutes.</p>",
)
PHISH = RawEmailMessage(
    provider_message_id="m3",
    subject="Verify your account",
    sender="support@myworkday-careers.com",
    received_at=SINCE,
    snippet="",
    body="<a href='https://acme.wd5.myworkdayjobs.com/activate?token=zzz'>Verify</a>",
)


def test_extract_takes_only_the_sites_own_link() -> None:
    found = inbox.extract(WORKDAY_VERIFY, HOST, "verify_email")
    assert found.link == "https://acme.wd5.myworkdayjobs.com/en-US/External/activate?token=abc&x=1"
    assert found.code is None and found.sender == "acme@myworkday.com"
    assert inbox.extract(WORKDAY_VERIFY, HOST, "reset_password") is None


def test_extract_codes_and_ignores_other_senders() -> None:
    assert inbox.extract(CODE_MAIL, HOST, "sign_in_code").code == "482913"
    assert inbox.extract(PHISH, HOST, "verify_email") is None
    assert inbox.extract(WORKDAY_VERIFY, "jobs.other.com", "verify_email") is None


def test_trusted_domains() -> None:
    assert inbox.trusted_domains(HOST) == ["myworkday.com", "myworkdayjobs.com", "workday.com"]
    assert inbox.trusted_domains("careers.acme.co.uk") == ["acme.co.uk"]


class FakeInbox:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def search_recent(self, *, senders, since, max_messages=10):
        self.calls.append((senders, since))
        return self.batches.pop(0) if self.batches else []


@pytest.mark.asyncio
async def test_wait_for_email_polls_until_it_arrives(monkeypatch, user_id) -> None:
    client = FakeInbox([[], [PHISH], [CODE_MAIL]])
    monkeypatch.setattr(inbox, "inbox_client", lambda session, uid: (client, None))
    found = await inbox.wait_for_email(user_id, HOST, "sign_in_code", SINCE, interval=0.01)
    assert found.code == "482913" and len(client.calls) == 3
    skipped = await inbox.wait_for_email(
        user_id, HOST, "sign_in_code", SINCE, skip={"m2"}, timeout=0.05, interval=0.01
    )
    assert skipped is None


@pytest.mark.asyncio
async def test_wait_for_email_needs_an_inbox(user_id) -> None:
    with pytest.raises(inbox.InboxUnavailable, match="No inbox"):
        await inbox.wait_for_email(user_id, HOST, "verify_email", SINCE, timeout=0)


def test_gmail_search_recent_queries_the_site_senders() -> None:
    client = GmailClient(client_id="cid", client_secret="secret", refresh_token="refresh")
    seen: list[dict] = []

    def fake_request(method, url, **kwargs):
        url = str(url)
        if "oauth2.googleapis.com/token" in url:
            return httpx.Response(
                200, json={"access_token": "t"}, request=httpx.Request(method, url)
            )
        if url.endswith("/users/me/messages"):
            seen.append(kwargs["params"])
            return httpx.Response(
                200, json={"messages": [{"id": "x1"}]}, request=httpx.Request(method, url)
            )
        return httpx.Response(
            200,
            json={
                "payload": {
                    "headers": [{"name": "From", "value": "a@myworkday.com"}],
                    "mimeType": "text/plain",
                    "body": {"data": "aGVsbG8="},
                }
            },
            request=httpx.Request(method, url),
        )

    since = SINCE - timedelta(minutes=10)
    with patch.object(client._http, "request", side_effect=fake_request):
        messages = client.search_recent(senders=["myworkday.com", "workday.com"], since=since)
    assert [m.body for m in messages] == ["hello"]
    assert seen[0]["q"] == (f"after:{int(since.timestamp())} from:(myworkday.com OR workday.com)")
