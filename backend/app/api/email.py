"""Email inbox connection and sync — user credentials in SQLite, Gmail OAuth in-app."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..schemas import (
    EmailConnectRequest,
    EmailConnectResponse,
    EmailDisconnectResponse,
    EmailGmailAuthorizeResponse,
    EmailGmailSetupRequest,
    EmailGmailSetupResponse,
    EmailGmailSetupSaveResponse,
    EmailStatusResponse,
)
from ..services.app_config import (
    get_config_value,
    gmail_oauth_configured,
    resolve_gmail_credentials,
    save_gmail_oauth_setup,
)
from ..services.email_client import (
    EmailClientError,
    EmailNotConfiguredError,
    build_email_client,
)
from ..services.email_connection import (
    clear_email_connection,
    connection_from_state,
    gmail_oauth_ready,
    mailbox_label,
    save_gmail_connection,
    save_imap_connection,
    status_provider,
    user_is_connected,
)
from ..services.email_sync import get_or_create_email_sync_state
from ..services.gmail_oauth import (
    GMAIL_API_ENABLE_URL,
    GOOGLE_CLOUD_CONSOLE,
    GOOGLE_OAUTH_CLIENTS_URL,
    build_authorization_url,
    exchange_authorization_code,
    new_oauth_state,
    oauth_state_is_popup,
)
from ..services.gmail_oauth_ui import oauth_popup_result_html
from .profile import get_or_create_local_user

router = APIRouter(prefix="/email", tags=["email"])


def _mask_history_id(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 6:
        return value
    return f"...{value[-6:]}"


def _apply_cursor_updates(state, updates: dict[str, object]) -> None:
    for key in ("provider", "gmail_history_id", "imap_uid_validity", "imap_last_uid"):
        if key in updates:
            setattr(state, key, updates[key])


def _gmail_credentials_or_503(session: Session):
    credentials = resolve_gmail_credentials(session)
    if credentials is None:
        raise HTTPException(
            status_code=503,
            detail="Gmail sign-in is not available on this install.",
        )
    return credentials


def _build_email_client_for_state(session: Session, sync_state):
    settings = get_settings()
    credentials = resolve_gmail_credentials(session)
    return build_email_client(
        connection_from_state(sync_state),  # type: ignore[arg-type]
        app_settings=settings,
        gmail_client_id=credentials.client_id if credentials else None,
        gmail_client_secret=credentials.client_secret if credentials else None,
    )


@router.get("/status", response_model=EmailStatusResponse)
def email_status(session: Session = Depends(get_session)) -> EmailStatusResponse:
    settings = get_settings()
    user = get_or_create_local_user(session)
    sync_state = get_or_create_email_sync_state(session, user.id)
    connected = user_is_connected(sync_state)
    return EmailStatusResponse(
        connected=connected,
        provider=status_provider(sync_state),  # type: ignore[arg-type]
        configured=connected,
        connected_email=sync_state.connected_email,
        gmail_oauth_available=gmail_oauth_ready(settings, session),
        last_sync_at=sync_state.last_sync_at,
        gmail_history_id=_mask_history_id(sync_state.gmail_history_id),
        imap_last_uid=sync_state.imap_last_uid,
        mailbox=mailbox_label(sync_state),
    )


@router.get("/gmail/setup", response_model=EmailGmailSetupResponse)
def gmail_setup(session: Session = Depends(get_session)) -> EmailGmailSetupResponse:
    """One-time OAuth app setup info for Continue with Google."""

    settings = get_settings()
    configured = gmail_oauth_configured(session)
    redirect_uri = settings.gmail_oauth_redirect_uri.strip()
    has_client_id = bool(
        settings.gmail_client_id.strip() or get_config_value(session, "gmail_client_id")
    )
    return EmailGmailSetupResponse(
        configured=configured,
        redirect_uri=redirect_uri,
        has_client_id=has_client_id,
        setup_steps=[
            "Open Google Cloud Console and create or pick a project.",
            "Enable the Gmail API for that project.",
            "Create an OAuth client (type: Web application).",
            f"Add this authorized redirect URI: {redirect_uri}",
            "Paste the Client ID and Client Secret below, then connect Gmail.",
        ],
        google_cloud_console_url=GOOGLE_CLOUD_CONSOLE,
        gmail_api_enable_url=GMAIL_API_ENABLE_URL,
        oauth_clients_url=GOOGLE_OAUTH_CLIENTS_URL,
    )


@router.post("/gmail/setup", response_model=EmailGmailSetupSaveResponse)
def save_gmail_setup(
    payload: EmailGmailSetupRequest,
    session: Session = Depends(get_session),
) -> EmailGmailSetupSaveResponse:
    """Save OAuth app credentials from the in-app setup wizard."""

    save_gmail_oauth_setup(
        session,
        client_id=payload.client_id.strip(),
        client_secret=payload.client_secret.strip(),
    )
    session.commit()
    return EmailGmailSetupSaveResponse(
        ok=True,
        configured=True,
        message="Google setup saved. You can now connect Gmail.",
    )


@router.get("/gmail/authorize", response_model=EmailGmailAuthorizeResponse)
def gmail_authorize(
    session: Session = Depends(get_session),
    popup: bool = Query(default=False),
) -> EmailGmailAuthorizeResponse:
    """Start Gmail OAuth — returns a URL the user opens to grant inbox read access."""

    user = get_or_create_local_user(session)
    sync_state = get_or_create_email_sync_state(session, user.id)
    credentials = _gmail_credentials_or_503(session)

    state = new_oauth_state(popup=popup)
    sync_state.oauth_state = state
    session.commit()

    url = build_authorization_url(credentials, state=state)
    return EmailGmailAuthorizeResponse(authorization_url=url)


@router.get("/gmail/callback", include_in_schema=False)
def gmail_callback(
    session: Session = Depends(get_session),
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """OAuth redirect target — exchanges code, saves tokens, completes popup or Settings redirect."""

    settings = get_settings()
    frontend = settings.frontend_url.rstrip("/")
    user = get_or_create_local_user(session)
    sync_state = get_or_create_email_sync_state(session, user.id)
    popup = oauth_state_is_popup(state)

    def finish(*, ok: bool, message: str, redirect_query: str) -> HTMLResponse | RedirectResponse:
        if popup:
            return HTMLResponse(
                oauth_popup_result_html(ok=ok, message=message, target_origin=frontend)
            )
        if ok:
            return RedirectResponse(f"{frontend}/settings?email_connected=gmail")
        return RedirectResponse(f"{frontend}/settings?email_error={redirect_query}")

    if error:
        return finish(ok=False, message=f"Google sign-in was cancelled ({error}).", redirect_query=error)

    if not code or not state:
        return finish(ok=False, message="Missing authorization code from Google.", redirect_query="missing_code")

    if not sync_state.oauth_state or state != sync_state.oauth_state:
        return finish(ok=False, message="Invalid OAuth state. Please try again.", redirect_query="invalid_state")

    sync_state.oauth_state = None

    try:
        credentials = _gmail_credentials_or_503(session)
        refresh_token, email_address = exchange_authorization_code(credentials, code)
        save_gmail_connection(sync_state, refresh_token=refresh_token, connected_email=email_address)
        connection = connection_from_state(sync_state)
        if connection is None:
            raise EmailClientError("Failed to save Gmail connection.")
        client = build_email_client(
            connection,
            app_settings=settings,
            gmail_client_id=credentials.client_id,
            gmail_client_secret=credentials.client_secret,
        )
        client.ping()
    except HTTPException as e:
        session.commit()
        return finish(ok=False, message=str(e.detail), redirect_query="setup_required")
    except (EmailClientError, EmailNotConfiguredError) as e:
        session.commit()
        detail = str(e)[:180]
        return finish(ok=False, message=detail, redirect_query=detail)

    session.commit()
    label = sync_state.connected_email or "your Gmail account"
    return finish(ok=True, message=f"Connected {label}.", redirect_query="")


@router.post("/connect/imap", response_model=EmailConnectResponse)
def connect_imap(
    payload: EmailConnectRequest,
    session: Session = Depends(get_session),
) -> EmailConnectResponse:
    """Connect an IMAP inbox; credentials are stored for the local user only."""

    settings = get_settings()
    user = get_or_create_local_user(session)
    sync_state = get_or_create_email_sync_state(session, user.id)

    save_imap_connection(
        sync_state,
        host=payload.imap_host,
        user=payload.imap_user,
        app_password=payload.imap_app_password,
        folder=payload.imap_folder,
    )

    connection = connection_from_state(sync_state)
    if connection is None:
        raise HTTPException(status_code=500, detail="Failed to save IMAP connection.")

    try:
        client = build_email_client(connection, app_settings=settings)
        client.ping()
    except EmailNotConfiguredError as e:
        clear_email_connection(sync_state)
        session.commit()
        raise HTTPException(status_code=400, detail=str(e)) from e
    except EmailClientError as e:
        clear_email_connection(sync_state)
        session.commit()
        raise HTTPException(status_code=502, detail=str(e)) from e

    session.commit()
    session.refresh(sync_state)
    return EmailConnectResponse(
        ok=True,
        provider="imap",
        message="IMAP inbox connected.",
        connected_email=sync_state.connected_email,
    )


@router.post("/disconnect", response_model=EmailDisconnectResponse)
def disconnect_email(session: Session = Depends(get_session)) -> EmailDisconnectResponse:
    user = get_or_create_local_user(session)
    sync_state = get_or_create_email_sync_state(session, user.id)
    clear_email_connection(sync_state)
    session.commit()
    return EmailDisconnectResponse()
