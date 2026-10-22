"""Inbound Telegram handling: pairing, commands, button taps and free-text replies.

Only the linked chat (both chat id and sender id) is acted on. Buttons carry an
opaque, single-use nonce (`t:<nonce>`) resolved through `callback_tokens`;
other modules register what each action does with `register_action`.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from sqlalchemy import exists, func, select
from sqlalchemy.orm import aliased

from ..db import SessionLocal
from ..llm.usage import spend_summary
from ..models import Application, ApplicationStatus, ApplyRun, CallbackToken
from ..pipeline.state import is_paused, set_paused

if TYPE_CHECKING:
    from .service import MessagingService

logger = logging.getLogger(__name__)

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)

HELP_TEXT = (
    "🐝 <b>JobBee</b> finds jobs, tailors your CV and applies after you approve.\n\n"
    "/status — what's going on\n"
    "/pause — stop searching and applying\n"
    "/resume — start again\n"
    "/help — this message\n\n"
    "Send me a job link any time and I'll check it for you."
)


@dataclass
class ActionContext:
    user_id: int
    token: CallbackToken
    chat_id: str
    message_id: str | None


ActionHandler = Callable[[ActionContext], Awaitable[None]]
TextHandler = Callable[[int, str], Awaitable[None]]


class BotRouter:
    def __init__(self, service: MessagingService) -> None:
        self._service = service
        self._actions: dict[str, ActionHandler] = {}
        self._reply_waiters: dict[int, TextHandler] = {}
        self._url_handler: Callable[[int, str], Awaitable[None]] | None = None
        self._text_handler: TextHandler | None = None

    # --- registration ------------------------------------------------------------------

    def register_action(self, action: str, handler: ActionHandler) -> None:
        self._actions[action] = handler

    def expect_reply(self, user_id: int, handler: TextHandler) -> None:
        """The next free-text message from this user goes to `handler` (e.g. an agent question)."""

        self._reply_waiters[user_id] = handler

    def cancel_reply(self, user_id: int, handler: TextHandler | None = None) -> None:
        if handler is None or self._reply_waiters.get(user_id) is handler:
            self._reply_waiters.pop(user_id, None)

    def set_url_handler(self, handler: Callable[[int, str], Awaitable[None]]) -> None:
        self._url_handler = handler

    def set_text_handler(self, handler: TextHandler) -> None:
        self._text_handler = handler

    # --- dispatch -----------------------------------------------------------------------

    async def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            await self._on_callback(update["callback_query"])
        elif "message" in update:
            await self._on_message(update["message"])

    def _linked_user(self, chat_id: str, from_id: str) -> int | None:
        link = self._service.linked()
        if link is None or link.chat_id != chat_id or link.from_id != from_id:
            return None
        return link.user_id

    async def _reply(self, chat_id: str, text: str) -> None:
        if self._service.channel is not None:
            await self._service.channel.send_text(chat_id, text, html=True)

    async def _on_message(self, message: dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        from_id = str(message.get("from", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            return

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            if len(parts) == 2:
                user_id = self._service.redeem_link_code(
                    parts[1].strip(), chat_id=chat_id, from_id=from_id
                )
                if user_id is not None:
                    self._service.log(user_id, "in", text, kind="command")
                    await self._service.notify(
                        user_id,
                        "✅ Connected! I'll message you here when I find jobs worth your time, "
                        "and before anything is sent.\n\n" + HELP_TEXT,
                        html=True,
                        kind="welcome",
                    )
                    return
            if self._linked_user(chat_id, from_id) is None:
                await self._reply(
                    chat_id, "To connect, use the <b>Open Telegram</b> button in JobBee's setup."
                )
                return

        user_id = self._linked_user(chat_id, from_id)
        if user_id is None:
            await self._reply(chat_id, "This is a private JobBee assistant.")
            return

        self._service.log(user_id, "in", text, kind="command" if text.startswith("/") else "text")
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        if command in ("/start", "/help"):
            await self._service.notify(user_id, HELP_TEXT, html=True, kind="help")
        elif command == "/status":
            await self._service.notify(user_id, status_text(), html=True, kind="status")
        elif command == "/pause":
            set_paused(True)
            await self._service.notify(
                user_id, "⏸ Paused. I won't search or apply until you send /resume.", kind="status"
            )
        elif command == "/resume":
            set_paused(False)
            await self._service.notify(user_id, "▶️ Back to work.", kind="status")
        elif command is not None:
            await self._service.notify(user_id, HELP_TEXT, html=True, kind="help")
        elif user_id in self._reply_waiters:
            handler = self._reply_waiters.pop(user_id)
            await handler(user_id, text)
        elif (match := URL_RE.search(text)) and self._url_handler is not None:
            await self._url_handler(user_id, match.group(0).rstrip(".,);]"))
        elif self._text_handler is not None:
            await self._text_handler(user_id, text)
        else:
            await self._service.notify(user_id, HELP_TEXT, html=True, kind="help")

    async def _on_callback(self, query: dict) -> None:
        callback_id = str(query.get("id", ""))
        message = query.get("message") or {}
        chat_id = str(message.get("chat", {}).get("id", ""))
        from_id = str(query.get("from", {}).get("id", ""))
        message_id = str(message["message_id"]) if "message_id" in message else None
        data = query.get("data") or ""
        channel = self._service.channel

        user_id = self._linked_user(chat_id, from_id)
        if user_id is None or not data.startswith("t:"):
            if channel is not None:
                await channel.answer_callback(callback_id, "Not available.")
            return

        token = self._service.consume(data[2:], user_id)
        handler = self._actions.get(token.action) if token is not None else None
        if token is None or handler is None:
            if channel is not None:
                await channel.answer_callback(callback_id, "This button has expired.")
            await self._service.clear_buttons(user_id, message_id)
            return

        if channel is not None:
            await channel.answer_callback(callback_id)
        self._service.log(user_id, "in", f"[tap] {token.action}", kind="tap")
        await handler(
            ActionContext(user_id=user_id, token=token, chat_id=chat_id, message_id=message_id)
        )


def status_text() -> str:
    week_ago = datetime.now(timezone.utc) - timedelta(days=7)
    with SessionLocal() as session:
        counts = dict(
            session.execute(
                select(Application.status, func.count()).group_by(Application.status)
            ).all()
        )
        applied_week = session.execute(
            select(func.count())
            .select_from(Application)
            .where(
                Application.status == ApplicationStatus.submitted,
                Application.submitted_at >= week_ago,
            )
        ).scalar_one()
        waiting_runs = session.execute(
            select(func.count())
            .select_from(ApplyRun)
            .where(ApplyRun.status.in_(["needs_user", "needs_human", "awaiting_submit"]))
        ).scalar_one()
        newer = aliased(ApplyRun)
        set_aside = session.execute(
            select(func.count())
            .select_from(ApplyRun)
            .join(Application, Application.id == ApplyRun.application_id)
            .where(
                ApplyRun.status == "needs_you",
                Application.status == ApplicationStatus.approved,
                ~exists().where(
                    newer.application_id == ApplyRun.application_id, newer.id > ApplyRun.id
                ),
            )
        ).scalar_one()
    spend = spend_summary()
    state = "⏸ <b>Paused</b> — send /resume to restart" if is_paused() else "🐝 <b>Active</b>"
    lines = [
        state,
        f"• Waiting for your approval: {counts.get(ApplicationStatus.awaiting_approval, 0)}",
        f"• Waiting on you mid-application: {waiting_runs}",
        f"• Set aside for when you're at your PC: {set_aside}",
        f"• Approved, in the apply queue: {counts.get(ApplicationStatus.approved, 0)}",
        f"• Applied in the last 7 days: {applied_week}",
        (
            f"• Claude spend: ${spend['today_usd']:.2f} today "
            f"(budget ${spend['daily_budget_usd']:.0f}) · ${spend['month_usd']:.2f} this month"
        ),
    ]
    return "\n".join(
        html.escape(line, quote=False) if "<b>" not in line else line for line in lines
    )
