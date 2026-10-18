"""Gmail OAuth helpers for in-app user connect flow."""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx

from .app_config import GmailOAuthCredentials
from .email_client import EmailClientError, OAUTH_TOKEN_URL

GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GOOGLE_CLOUD_CONSOLE = "https://console.cloud.google.com/"
GMAIL_API_ENABLE_URL = (
    "https://console.cloud.google.com/apis/library/gmail.googleapis.com"
)
GOOGLE_OAUTH_CLIENTS_URL = (
    "https://console.cloud.google.com/apis/credentials/oauthclient"
)


def new_oauth_state(*, popup: bool = False) -> str:
    token = secrets.token_urlsafe(24)
    return f"popup:{token}" if popup else token


def oauth_state_is_popup(state: str | None) -> bool:
    return bool(state and state.startswith("popup:"))


def build_authorization_url(credentials: GmailOAuthCredentials, *, state: str) -> str:
    params = {
        "client_id": credentials.client_id,
        "redirect_uri": credentials.redirect_uri,
        "response_type": "code",
        "scope": GMAIL_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GMAIL_AUTH_URL}?{urlencode(params)}"


def exchange_authorization_code(
    credentials: GmailOAuthCredentials,
    code: str,
) -> tuple[str, str | None]:
    """Exchange auth code for tokens. Returns (refresh_token, email_address)."""

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            OAUTH_TOKEN_URL,
            data={
                "client_id": credentials.client_id,
                "client_secret": credentials.client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": credentials.redirect_uri,
            },
        )
        if resp.status_code != 200:
            raise EmailClientError(f"Gmail OAuth token exchange failed: {resp.status_code} {resp.text}")

        data = resp.json()
        refresh_token = data.get("refresh_token")
        access_token = data.get("access_token")
        if not refresh_token:
            raise EmailClientError(
                "Google did not return a refresh token. Try disconnecting the app in your "
                "Google Account permissions and connect again."
            )

        email_address: str | None = None
        if access_token:
            profile_resp = client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            if profile_resp.status_code == 200:
                email_address = profile_resp.json().get("emailAddress")

        return refresh_token, email_address
