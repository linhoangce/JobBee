"""Channel-neutral messaging types. Telegram implements them today; WhatsApp later."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Button:
    """A tap target: `data` (handled by the bot) or `url` (opens a link)."""

    text: str
    data: str | None = None
    url: str | None = None


Keyboard = list[list[Button]]


class Channel(Protocol):
    name: str

    async def send_text(
        self, chat_id: str, text: str, buttons: Keyboard | None = None, *, html: bool = False
    ) -> str: ...

    async def send_document(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        buttons: Keyboard | None = None,
        *,
        html: bool = False,
    ) -> str: ...

    async def send_photo(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        buttons: Keyboard | None = None,
        *,
        html: bool = False,
    ) -> str: ...

    async def clear_buttons(self, chat_id: str, message_id: str) -> None: ...

    async def answer_callback(self, callback_id: str, text: str | None = None) -> None: ...
