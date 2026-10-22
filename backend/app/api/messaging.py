"""Connect the user's messaging channel (Telegram).

GET    /api/messaging/status
POST   /api/messaging/telegram         {token} → validates, stores in keyring, returns link
POST   /api/messaging/telegram/link    → fresh one-time pairing link
POST   /api/messaging/test             → sends a test message to the linked chat
DELETE /api/messaging/telegram         → forget the bot and the pairing
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_session
from ..messaging.service import messaging
from ..messaging.telegram import TelegramError
from ..pipeline.state import is_paused
from .profile import get_or_create_local_user

router = APIRouter(prefix="/messaging", tags=["messaging"])


class TelegramSetupRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class MessagingStatusResponse(BaseModel):
    channel: str = "telegram"
    bot_configured: bool
    bot_username: str | None = None
    linked: bool
    linked_at: str | None = None
    polling: bool
    last_error: str | None = None
    paused: bool


class LinkResponse(BaseModel):
    bot_username: str
    link_url: str


@router.get("/status", response_model=MessagingStatusResponse)
def messaging_status(session: Session = Depends(get_session)) -> MessagingStatusResponse:
    user = get_or_create_local_user(session)
    link = messaging.linked(user.id)
    return MessagingStatusResponse(
        bot_configured=messaging.bot_token() is not None,
        bot_username=messaging.bot_username(),
        linked=link is not None,
        linked_at=link.linked_at.isoformat() if link and link.linked_at else None,
        polling=messaging.polling,
        last_error=messaging.last_error,
        paused=is_paused(),
    )


@router.post("/telegram", response_model=LinkResponse)
async def setup_telegram(
    payload: TelegramSetupRequest, session: Session = Depends(get_session)
) -> LinkResponse:
    user = get_or_create_local_user(session)
    try:
        username = await messaging.configure(payload.token.strip())
    except TelegramError as e:
        raise HTTPException(status_code=400, detail=f"Telegram rejected the token: {e}") from e
    code = messaging.new_link_code(user.id)
    return LinkResponse(bot_username=username, link_url=messaging.link_url(code))


@router.post("/telegram/link", response_model=LinkResponse)
def new_telegram_link(session: Session = Depends(get_session)) -> LinkResponse:
    user = get_or_create_local_user(session)
    username = messaging.bot_username()
    if messaging.bot_token() is None or not username:
        raise HTTPException(status_code=400, detail="Add your Telegram bot token first.")
    code = messaging.new_link_code(user.id)
    return LinkResponse(bot_username=username, link_url=messaging.link_url(code))


@router.post("/test", status_code=status.HTTP_204_NO_CONTENT)
async def send_test_message(session: Session = Depends(get_session)) -> None:
    user = get_or_create_local_user(session)
    if messaging.linked(user.id) is None:
        raise HTTPException(status_code=400, detail="Telegram isn't connected yet.")
    sent = await messaging.notify(user.id, "👋 Test message from JobBee.", kind="test")
    if sent is None:
        raise HTTPException(status_code=502, detail=messaging.last_error or "Send failed.")


@router.delete("/telegram", status_code=status.HTTP_204_NO_CONTENT)
async def forget_telegram() -> None:
    await messaging.forget()
