"""Messaging service: the long-poll loop, outbound notifications, pairing and button tokens.

One process-wide instance (`messaging`) is started from the FastAPI lifespan.
Inbound updates go to `BotRouter` (bot.py). Handlers must return quickly; slow
work goes through `spawn()` so polling never stalls.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from collections.abc import Coroutine
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select, update

from ..db import SessionLocal
from ..models import CallbackToken, ChatMessage, MessagingLink, User
from ..pipeline.state import get_flag, set_flag
from ..services.secrets import get_secret_store
from .base import Button, Keyboard
from .telegram import TelegramChannel, TelegramError

logger = logging.getLogger(__name__)

CHANNEL = "telegram"
BOT_TOKEN_SECRET = "app:telegram_bot_token"
BOT_USERNAME_KEY = "telegram_bot_username"
OFFSET_KEY = "telegram_offset"
LINK_CODE_TTL = timedelta(minutes=10)
BUTTON_TTL = timedelta(days=3)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class MessagingService:
    def __init__(self) -> None:
        from .bot import BotRouter

        self.channel: TelegramChannel | None = None
        self.router = BotRouter(self)
        self._poller: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self.last_poll_ok: datetime | None = None
        self.last_error: str | None = None

    # --- configuration ----------------------------------------------------------

    @staticmethod
    def bot_token() -> str | None:
        token = get_secret_store().get(BOT_TOKEN_SECRET)
        return token.strip() if token and token.strip() else None

    @staticmethod
    def bot_username() -> str | None:
        return get_flag(BOT_USERNAME_KEY)

    async def configure(self, token: str) -> str:
        """Validate a bot token with getMe, store it in the keyring, restart polling."""

        probe = TelegramChannel(token)
        try:
            me = await probe.get_me()
        finally:
            await probe.aclose()
        username = me.get("username")
        if not username or not me.get("is_bot"):
            raise TelegramError("That token doesn't belong to a bot.")
        get_secret_store().set(BOT_TOKEN_SECRET, token)
        set_flag(BOT_USERNAME_KEY, username)
        set_flag(OFFSET_KEY, "0")
        await self.restart()
        return username

    async def forget(self) -> None:
        await self.stop()
        get_secret_store().delete(BOT_TOKEN_SECRET)
        with SessionLocal() as session:
            session.execute(
                update(MessagingLink).values(chat_id=None, from_id=None, linked_at=None)
            )
            session.commit()

    # --- lifecycle --------------------------------------------------------------------

    async def start(self) -> None:
        token = self.bot_token()
        if token is None or self._poller is not None:
            return
        self.channel = TelegramChannel(token)
        self._poller = asyncio.get_running_loop().create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._poller is not None:
            self._poller.cancel()
            await asyncio.gather(self._poller, return_exceptions=True)
            self._poller = None
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.channel is not None:
            await self.channel.aclose()
            self.channel = None

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    @property
    def polling(self) -> bool:
        return self._poller is not None and not self._poller.done()

    def spawn(self, coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error("Messaging task failed", exc_info=t.exception())

        task.add_done_callback(_done)
        return task

    async def _poll_loop(self) -> None:
        backoff = 2.0
        offset = int(get_flag(OFFSET_KEY) or 0) or None
        while True:
            try:
                updates = await self.channel.get_updates(offset)
                self.last_poll_ok = _now()
                self.last_error = None
                backoff = 2.0
            except TelegramError as e:
                self.last_error = str(e)
                logger.warning("Telegram polling failed: %s", e)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            for item in updates:
                offset = int(item["update_id"]) + 1
                set_flag(OFFSET_KEY, str(offset))  # before handling: never replay a tap
                try:
                    await self.router.handle_update(item)
                except Exception:
                    logger.exception("Handling Telegram update failed")

    # --- pairing ---------------------------------------------------------------------

    def new_link_code(self, user_id: int) -> str:
        code = secrets.token_urlsafe(12)
        with SessionLocal() as session:
            link = session.execute(
                select(MessagingLink).where(
                    MessagingLink.user_id == user_id, MessagingLink.channel == CHANNEL
                )
            ).scalar_one_or_none()
            if link is None:
                link = MessagingLink(user_id=user_id, channel=CHANNEL)
                session.add(link)
            link.link_code_hash = _hash(code)
            link.link_expires_at = _now() + LINK_CODE_TTL
            session.commit()
        return code

    def link_url(self, code: str) -> str | None:
        username = self.bot_username()
        return f"https://t.me/{username}?start={code}" if username else None

    def redeem_link_code(self, code: str, *, chat_id: str, from_id: str) -> int | None:
        """Bind a chat to the user that owns `code`; returns the user id or None."""

        with SessionLocal() as session:
            link = session.execute(
                select(MessagingLink).where(
                    MessagingLink.channel == CHANNEL,
                    MessagingLink.link_code_hash == _hash(code),
                    MessagingLink.link_expires_at > _now(),
                )
            ).scalar_one_or_none()
            if link is None:
                return None
            link.chat_id = chat_id
            link.from_id = from_id
            link.linked_at = _now()
            link.link_code_hash = None
            link.link_expires_at = None
            session.commit()
            return link.user_id

    @staticmethod
    def linked(user_id: int | None = None) -> MessagingLink | None:
        with SessionLocal() as session:
            stmt = select(MessagingLink).where(
                MessagingLink.channel == CHANNEL, MessagingLink.chat_id.is_not(None)
            )
            if user_id is not None:
                stmt = stmt.where(MessagingLink.user_id == user_id)
            return session.execute(stmt.order_by(MessagingLink.id)).scalars().first()

    # --- buttons -----------------------------------------------------------------------

    def button(
        self,
        user_id: int,
        text: str,
        action: str,
        *,
        target_id: int | None = None,
        payload: dict | None = None,
        group: str | None = None,
        ttl: timedelta = BUTTON_TTL,
    ) -> Button:
        nonce = secrets.token_urlsafe(9)
        with SessionLocal() as session:
            session.add(
                CallbackToken(
                    nonce=nonce,
                    user_id=user_id,
                    action=action,
                    target_id=target_id,
                    payload=payload or {},
                    group=group,
                    expires_at=_now() + ttl,
                )
            )
            session.commit()
        return Button(text=text, data=f"t:{nonce}")

    @staticmethod
    def new_group() -> str:
        return secrets.token_urlsafe(9)

    @staticmethod
    def consume(nonce: str, user_id: int) -> CallbackToken | None:
        """Spend a button token exactly once (and its whole group). None if used/expired."""

        now = _now()
        with SessionLocal() as session:
            result = session.execute(
                update(CallbackToken)
                .where(
                    CallbackToken.nonce == nonce,
                    CallbackToken.user_id == user_id,
                    CallbackToken.used_at.is_(None),
                    CallbackToken.expires_at > now,
                )
                .values(used_at=now)
            )
            if result.rowcount != 1:
                session.rollback()
                return None
            token = session.get(CallbackToken, nonce)
            if token.group:
                session.execute(
                    update(CallbackToken)
                    .where(CallbackToken.group == token.group, CallbackToken.used_at.is_(None))
                    .values(used_at=now)
                )
            session.commit()
            session.refresh(token)
            session.expunge(token)
            return token

    # --- outbound ------------------------------------------------------------------------

    @staticmethod
    def log(
        user_id: int,
        direction: str,
        text: str,
        *,
        kind: str = "text",
        external_id: str | None = None,
        application_id: int | None = None,
        run_id: int | None = None,
    ) -> None:
        with SessionLocal() as session:
            session.add(
                ChatMessage(
                    user_id=user_id,
                    channel=CHANNEL,
                    direction=direction,
                    kind=kind,
                    text=text[:4000],
                    external_id=external_id,
                    application_id=application_id,
                    run_id=run_id,
                )
            )
            session.commit()

    async def notify(
        self,
        user_id: int,
        text: str,
        *,
        buttons: Keyboard | None = None,
        document: Path | None = None,
        photo: Path | None = None,
        html: bool = False,
        kind: str = "info",
        application_id: int | None = None,
        run_id: int | None = None,
    ) -> str | None:
        """Send to the user's linked chat (logged either way); returns the message id."""

        link = self.linked(user_id)
        message_id: str | None = None
        if link is not None and self.channel is not None:
            try:
                if document is not None:
                    message_id = await self.channel.send_document(
                        link.chat_id, document, text, buttons, html=html
                    )
                elif photo is not None:
                    message_id = await self.channel.send_photo(
                        link.chat_id, photo, text, buttons, html=html
                    )
                else:
                    message_id = await self.channel.send_text(
                        link.chat_id, text, buttons, html=html
                    )
            except TelegramError as e:
                logger.warning("Telegram send failed: %s", e)
                self.last_error = str(e)
        self.log(
            user_id,
            "out",
            text,
            kind=kind,
            external_id=message_id,
            application_id=application_id,
            run_id=run_id,
        )
        return message_id

    async def clear_buttons(self, user_id: int, message_id: str | None) -> None:
        link = self.linked(user_id)
        if link is None or self.channel is None or not message_id:
            return
        try:
            await self.channel.clear_buttons(link.chat_id, message_id)
        except TelegramError as e:
            logger.debug("Could not clear buttons: %s", e)

    @staticmethod
    def first_user_id() -> int | None:
        with SessionLocal() as session:
            return session.execute(select(User.id).order_by(User.id)).scalars().first()


messaging = MessagingService()
