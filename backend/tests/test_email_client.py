"""Tests for Gmail OAuth setup and user-centric email inbox (mocked — no real network)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.models import EmailSyncState
from app.services.email_client import (
    EmailNotConfiguredError,
    GmailClient,
    ImapClient,
    build_email_client,
)
from app.services.email_connection import (
    UserEmailConnection,
)
from app.services.gmail_oauth import build_authorization_url, oauth_state_is_popup
from app.services.gmail_oauth_ui import oauth_popup_result_html


def _app_settings() -> Settings:
    return Settings(
        gmail_client_id="cid",
        gmail_client_secret="secret",
        gmail_oauth_redirect_uri="http://127.0.0.1:8000/api/email/gmail/callback",
    )


def _gmail_connection() -> UserEmailConnection:
    return UserEmailConnection(
        provider="gmail",
        connected_email="user@gmail.com",
        gmail_refresh_token="refresh",
    )


def _imap_connection() -> UserEmailConnection:
    return UserEmailConnection(
        provider="imap",
        connected_email="user@example.com",
        imap_host="imap.example.com",
        imap_user="user@example.com",
        imap_app_password="app-pass",
        imap_folder="INBOX",
    )


def test_build_email_client_gmail_requires_credentials() -> None:
    empty_settings = Settings.model_validate({"gmail_client_id": "", "gmail_client_secret": ""})
    with pytest.raises(EmailNotConfiguredError):
        build_email_client(_gmail_connection(), app_settings=empty_settings)


def test_gmail_fetch_new_messages() -> None:
    client = GmailClient(client_id="cid", client_secret="secret", refresh_token="refresh")
    cursor = EmailSyncState(user_id=1, gmail_history_id=None)

    token_resp = httpx.Response(
        200,
        json={"access_token": "atoken", "expires_in": 3600},
        request=httpx.Request("POST", "https://oauth2.googleapis.com/token"),
    )
    list_resp = httpx.Response(
        200,
        json={"messages": [{"id": "msg1"}, {"id": "msg2"}]},
        request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages"),
    )
    msg_resp = httpx.Response(
        200,
        json={
            "snippet": "Backend role at Acme",
            "payload": {
                "headers": [
                    {"name": "From", "value": "jobs@linkedin.com"},
                    {"name": "Subject", "value": "New jobs"},
                    {"name": "Date", "value": "Thu, 01 Jan 2026 12:00:00 +0000"},
                ],
                "mimeType": "text/html",
                "body": {
                    "data": "PGJPRz5CYWNrZW5kIGF0IEFjbWU8L0JPRz4=",
                },
            },
        },
        request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/messages/msg1"),
    )
    profile_resp = httpx.Response(
        200,
        json={"historyId": "99999"},
        request=httpx.Request("GET", "https://gmail.googleapis.com/gmail/v1/users/me/profile"),
    )

    def fake_request(method, url, **kwargs):
        url_str = str(url)
        if "oauth2.googleapis.com/token" in url_str:
            return token_resp
        if url_str.endswith("/users/me/messages") and method == "GET":
            return list_resp
        if "/users/me/messages/msg" in url_str:
            return msg_resp
        if url_str.endswith("/users/me/profile"):
            return profile_resp
        raise AssertionError(f"Unexpected request: {method} {url_str}")

    with patch.object(client._http, "request", side_effect=fake_request):
        messages, updates = client.fetch_new_messages(cursor=cursor, max_messages=10)

    assert len(messages) == 2
    assert messages[0].provider_message_id == "msg1"
    assert updates["gmail_history_id"] == "99999"


def test_imap_fetch_advances_uid_cursor() -> None:
    client = ImapClient(_imap_connection())
    cursor = EmailSyncState(user_id=1, imap_last_uid=10, imap_uid_validity=42)

    mock_conn = MagicMock()
    mock_conn.select.return_value = ("OK", [b"1"])
    mock_conn.status.return_value = ("OK", [b"INBOX (UIDVALIDITY 42)"])
    mock_conn.uid.side_effect = [
        ("OK", [b"11 12"]),
        (
            "OK",
            [
                (
                    b"11 (RFC822)",
                    b"From: alert@indeed.com\r\nSubject: New job\r\n\r\nPython dev role",
                ),
            ],
        ),
        (
            "OK",
            [
                (
                    b"12 (RFC822)",
                    b"From: jobs@linkedin.com\r\nSubject: LinkedIn jobs\r\n\r\nEngineer opening",
                ),
            ],
        ),
    ]

    with patch("app.services.email_client.imaplib.IMAP4_SSL", return_value=mock_conn):
        messages, updates = client.fetch_new_messages(cursor=cursor, max_messages=10)

    assert len(messages) == 2
    assert updates["imap_last_uid"] == 12
    assert updates["imap_uid_validity"] == 42


def test_email_status_default() -> None:
    with TestClient(app) as client:
        resp = client.get("/api/email/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["connected"] is False
    assert body["provider"] == "disabled"


def test_gmail_setup_save_enables_oauth() -> None:
    with TestClient(app) as client:
        setup = client.get("/api/email/gmail/setup")
        assert setup.status_code == 200
        was_configured = setup.json()["configured"]

        saved = client.post(
            "/api/email/gmail/setup",
            json={"client_id": "test-client-id.apps.googleusercontent.com", "client_secret": "test-secret"},
        )
        assert saved.status_code == 200
        assert saved.json()["configured"] is True

        status = client.get("/api/email/status")
        assert status.json()["gmail_oauth_available"] is True
        assert was_configured in (True, False)


def test_gmail_authorize_returns_url() -> None:
    with TestClient(app) as client:
        client.post(
            "/api/email/gmail/setup",
            json={"client_id": "test-client-id.apps.googleusercontent.com", "client_secret": "test-secret"},
        )
        resp = client.get("/api/email/gmail/authorize?popup=1")
    assert resp.status_code == 200
    assert "accounts.google.com" in resp.json()["authorization_url"]


def test_connect_imap_saves_user_credentials() -> None:
    mock_client = MagicMock()

    with patch("app.api.email.build_email_client", return_value=mock_client):
        with TestClient(app) as client:
            resp = client.post(
                "/api/email/connect/imap",
                json={
                    "imap_host": "imap.example.com",
                    "imap_user": "u@example.com",
                    "imap_app_password": "secret",
                    "imap_folder": "INBOX",
                },
            )

    assert resp.status_code == 200
    assert resp.json()["provider"] == "imap"
    mock_client.ping.assert_called_once()

    with TestClient(app) as client:
        status = client.get("/api/email/status")
    assert status.json()["connected"] is True
    assert status.json()["connected_email"] == "u@example.com"


def test_disconnect_clears_connection() -> None:
    mock_client = MagicMock()
    with patch("app.api.email.build_email_client", return_value=mock_client):
        with TestClient(app) as client:
            client.post(
                "/api/email/connect/imap",
                json={
                    "imap_host": "imap.example.com",
                    "imap_user": "u@example.com",
                    "imap_app_password": "secret",
                },
            )
            client.post("/api/email/disconnect")
            status = client.get("/api/email/status")
    assert status.json()["connected"] is False


def test_oauth_popup_state_and_html() -> None:
    assert oauth_state_is_popup("popup:abc123")
    assert not oauth_state_is_popup("abc123")
    html = oauth_popup_result_html(ok=True, message="Connected user@gmail.com.")
    assert "jobbee:gmail-oauth" in html
    assert "Connected user@gmail.com." in html


def test_build_authorization_url_uses_credentials() -> None:
    from app.services.app_config import GmailOAuthCredentials

    creds = GmailOAuthCredentials(
        client_id="abc.apps.googleusercontent.com",
        client_secret="secret",
        redirect_uri="http://127.0.0.1:8000/api/email/gmail/callback",
    )
    url = build_authorization_url(creds, state="popup:token")
    assert "client_id=abc.apps.googleusercontent.com" in url
    assert "gmail.readonly" in url


def test_imap_password_goes_to_secret_store_not_database_or_responses() -> None:
    from sqlalchemy import inspect as sa_inspect

    from app.api.profile import get_or_create_local_user
    from app.db import SessionLocal, engine
    from app.services.secrets import IMAP_APP_PASSWORD, get_user_secret

    mock_client = MagicMock()
    with patch("app.api.email.build_email_client", return_value=mock_client):
        with TestClient(app) as client:
            resp = client.post(
                "/api/email/connect/imap",
                json={
                    "imap_host": "imap.example.com",
                    "imap_user": "u@example.com",
                    "imap_app_password": "super-secret-pass",
                },
            )
            status = client.get("/api/email/status")

    assert resp.status_code == 200
    assert status.json()["connected"] is True
    assert "super-secret-pass" not in resp.text
    assert "super-secret-pass" not in status.text
    columns = {c["name"] for c in sa_inspect(engine).get_columns("email_sync_states")}
    assert "imap_app_password" not in columns
    with SessionLocal() as session:
        user = get_or_create_local_user(session)
        assert get_user_secret(user.id, IMAP_APP_PASSWORD) == "super-secret-pass"
