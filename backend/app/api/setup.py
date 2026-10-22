"""First-run setup and the Claude connection.

GET    /api/setup                what's done: Claude key, resume, onboarding, Telegram, inbox
POST   /api/setup/finish         end the welcome flow and run the first job search
GET    /api/setup/claude         key status, models and spend
PUT    /api/setup/claude-key     check a key (and workspace) with Anthropic; key → OS keyring
DELETE /api/setup/claude-key
PUT    /api/setup/budget         daily / monthly spend limits (saved to backend/.env)
"""

from __future__ import annotations

from datetime import datetime, timezone

from anthropic import APIConnectionError, APIStatusError, AsyncAnthropic, AuthenticationError
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_session
from ..llm.factory import reset_llm_provider
from ..llm.usage import spend_summary
from ..pipeline.discovery import discovery
from ..schemas import ActionResponse, BudgetUpdate, ClaudeKeyRequest, ClaudeStatus, SetupStatus
from ..pipeline.state import set_flag
from ..services.secrets import (
    ANTHROPIC_API_KEY,
    WORKSPACE_ID_KEY,
    SecretStoreError,
    claude_api_key,
    claude_headers,
    claude_workspace_id,
    get_secret_store,
)
from . import settings as settings_api
from .agent import require_ready
from .home import setup_status
from .profile import get_or_create_local_user

router = APIRouter(prefix="/setup", tags=["setup"])


WORKSPACE_HINT = (
    "This key works for your whole Anthropic organization, so JobBee also needs to know which "
    "workspace to use. Paste the workspace ID (Console → Settings → Workspaces, it starts with "
    "wrkspc_), or create a key inside a workspace instead."
)


def _api_message(error: APIStatusError) -> str:
    body = error.body if isinstance(error.body, dict) else {}
    detail = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
    return str(detail.get("message") or error.message)


async def validate_claude_key(api_key: str, workspace_id: str = "") -> None:
    """Raise HTTPException unless Anthropic accepts the key for real requests.

    Counting tokens is free and goes through the same checks as a message, so it also
    catches organization-wide keys that need a workspace.
    """

    client = AsyncAnthropic(
        api_key=api_key,
        base_url="https://api.anthropic.com",
        timeout=20.0,
        default_headers=claude_headers(workspace_id) or None,
    )
    try:
        await client.messages.count_tokens(
            model=get_settings().match_model,
            messages=[{"role": "user", "content": "ping"}],
        )
    except AuthenticationError as e:
        raise HTTPException(status_code=400, detail="Anthropic didn't accept that key.") from e
    except APIConnectionError as e:
        raise HTTPException(
            status_code=502, detail="Couldn't reach Anthropic. Check your connection."
        ) from e
    except APIStatusError as e:
        message = _api_message(e)
        if "workspace" in message.lower():
            detail = WORKSPACE_HINT if not workspace_id else f"Anthropic said: {message}"
            raise HTTPException(status_code=400, detail=detail) from e
        raise HTTPException(status_code=400, detail=f"Anthropic said: {message}") from e
    finally:
        await client.close()


def _stored_key() -> str | None:
    try:
        value = get_secret_store().get(ANTHROPIC_API_KEY)
    except SecretStoreError:
        return None
    return value.strip() if value and value.strip() else None


def _claude_status() -> ClaudeStatus:
    settings = get_settings()
    stored = _stored_key()
    source = "app" if stored else "env" if settings.anthropic_api_key.strip() else "none"
    spend = spend_summary()
    return ClaudeStatus(
        connected=source != "none",
        source=source,  # type: ignore[arg-type]
        models={
            "screening": settings.match_model,
            "tailoring": settings.tailor_model,
            "job search": settings.discover_model,
            "applying": settings.apply_model,
        },
        workspace_id=claude_workspace_id(),
        spend_today_usd=round(spend["today_usd"], 2),
        spend_month_usd=round(spend["month_usd"], 2),
        daily_budget_usd=spend["daily_budget_usd"],
        monthly_budget_usd=spend["monthly_budget_usd"],
    )


@router.get("", response_model=SetupStatus)
def read_setup(session: Session = Depends(get_session)) -> SetupStatus:
    return setup_status(session, get_or_create_local_user(session))


@router.post("/finish", response_model=ActionResponse)
async def finish_setup(session: Session = Depends(get_session)) -> ActionResponse:
    user = get_or_create_local_user(session)
    require_ready(session, user)
    if not user.target_roles:
        raise HTTPException(status_code=400, detail="Tell JobBee which roles you want first.")
    if user.onboarded_at is None:
        user.onboarded_at = datetime.now(timezone.utc)
        session.commit()
    started = discovery.start(user.id, "manual")
    return ActionResponse(
        message="Looking for your first jobs…" if started else "A search is already running."
    )


@router.get("/claude", response_model=ClaudeStatus)
def claude_status() -> ClaudeStatus:
    return _claude_status()


@router.put("/claude-key", response_model=ClaudeStatus)
async def save_claude_key(payload: ClaudeKeyRequest) -> ClaudeStatus:
    """Save a new key and/or the workspace for an organization-wide key (both are checked)."""

    key = (payload.api_key or "").strip() or claude_api_key()
    if not key:
        raise HTTPException(status_code=400, detail="Paste your Anthropic API key.")
    workspace = (
        claude_workspace_id() if payload.workspace_id is None else payload.workspace_id.strip()
    )
    await validate_claude_key(key, workspace)
    if payload.api_key:
        try:
            get_secret_store().set(ANTHROPIC_API_KEY, key)
        except SecretStoreError as e:
            raise HTTPException(
                status_code=500, detail="Couldn't save the key to Windows Credential Manager."
            ) from e
    set_flag(WORKSPACE_ID_KEY, workspace)
    reset_llm_provider()
    return _claude_status()


@router.delete("/claude-key", response_model=ClaudeStatus)
def forget_claude_key() -> ClaudeStatus:
    get_secret_store().delete(ANTHROPIC_API_KEY)
    set_flag(WORKSPACE_ID_KEY, "")
    reset_llm_provider()
    return _claude_status()


@router.put("/budget", response_model=ClaudeStatus)
def update_budget(payload: BudgetUpdate) -> ClaudeStatus:
    updates = {}
    if payload.daily_budget_usd is not None:
        updates["DAILY_BUDGET_USD"] = f"{payload.daily_budget_usd:g}"
    if payload.monthly_budget_usd is not None:
        updates["MONTHLY_BUDGET_USD"] = f"{payload.monthly_budget_usd:g}"
    if updates:
        settings_api.save_env(updates)
    return _claude_status()
