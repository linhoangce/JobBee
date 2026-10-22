"""Minimal Telegram Bot API client over httpx (long polling; no public webhook needed).

The bot token is part of every request URL, so errors raised from here never
include the URL or the underlying httpx exception text.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
from typing import Any

import httpx

from .base import Keyboard

API_ROOT = "https://api.telegram.org"
TEXT_LIMIT = 4096
CAPTION_LIMIT = 1024


class TelegramError(RuntimeError):
    pass


def _markup(buttons: Keyboard | None) -> dict | None:
    if not buttons:
        return None
    rows = []
    for row in buttons:
        cells = []
        for b in row:
            if b.url:
                cells.append({"text": b.text, "url": b.url})
            else:
                cells.append({"text": b.text, "callback_data": b.data or "noop"})
        rows.append(cells)
    return {"inline_keyboard": rows}


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


class TelegramChannel:
    name = "telegram"

    def __init__(self, token: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._base = f"{API_ROOT}/bot{token}"
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(40.0, connect=10.0))

    async def _call(
        self,
        method: str,
        *,
        data: dict[str, Any] | None = None,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        kwargs: dict[str, Any] = {}
        if files:
            form = {
                k: json.dumps(v) if isinstance(v, (dict, list)) else str(v)
                for k, v in (data or {}).items()
                if v is not None
            }
            kwargs.update(data=form, files=files)
        else:
            kwargs["json"] = {k: v for k, v in (data or {}).items() if v is not None}
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            resp = await self._client.post(f"{self._base}/{method}", **kwargs)
        except httpx.HTTPError as e:
            raise TelegramError(
                f"Telegram {method}: network error ({e.__class__.__name__})"
            ) from None
        try:
            payload = resp.json()
        except ValueError:
            raise TelegramError(f"Telegram {method}: HTTP {resp.status_code}") from None
        if not payload.get("ok"):
            raise TelegramError(
                f"Telegram {method}: {payload.get('description') or resp.status_code}"
            )
        return payload.get("result")

    async def get_me(self) -> dict:
        return await self._call("getMe")

    async def get_updates(self, offset: int | None, timeout: int = 25) -> list[dict]:
        return await self._call(
            "getUpdates",
            data={
                "offset": offset,
                "timeout": timeout,
                "allowed_updates": ["message", "callback_query"],
            },
            timeout=timeout + 15,
        )

    async def send_text(
        self, chat_id: str, text: str, buttons: Keyboard | None = None, *, html: bool = False
    ) -> str:
        result = await self._call(
            "sendMessage",
            data={
                "chat_id": chat_id,
                "text": _clip(text, TEXT_LIMIT),
                "parse_mode": "HTML" if html else None,
                "reply_markup": _markup(buttons),
                "link_preview_options": {"is_disabled": True},
            },
        )
        return str(result["message_id"])

    async def _send_file(
        self,
        method: str,
        field: str,
        chat_id: str,
        path: Path,
        caption: str,
        buttons: Keyboard | None,
        html: bool,
    ) -> str:
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        result = await self._call(
            method,
            data={
                "chat_id": chat_id,
                "caption": _clip(caption, CAPTION_LIMIT) if caption else None,
                "parse_mode": "HTML" if html and caption else None,
                "reply_markup": _markup(buttons),
            },
            files={field: (path.name, path.read_bytes(), mime)},
            timeout=120,
        )
        return str(result["message_id"])

    async def send_document(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        buttons: Keyboard | None = None,
        *,
        html: bool = False,
    ) -> str:
        return await self._send_file(
            "sendDocument", "document", chat_id, path, caption, buttons, html
        )

    async def send_photo(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        buttons: Keyboard | None = None,
        *,
        html: bool = False,
    ) -> str:
        return await self._send_file("sendPhoto", "photo", chat_id, path, caption, buttons, html)

    async def clear_buttons(self, chat_id: str, message_id: str) -> None:
        try:
            await self._call(
                "editMessageReplyMarkup",
                data={
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "reply_markup": {"inline_keyboard": []},
                },
            )
        except TelegramError as e:
            if "not modified" not in str(e) and "not found" not in str(e):
                raise

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None:
        await self._call(
            "answerCallbackQuery", data={"callback_query_id": callback_id, "text": text}
        )

    async def aclose(self) -> None:
        await self._client.aclose()
