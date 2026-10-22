"""Telegram client, bot router, pairing, button tokens, spend ledger and the Claude provider."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete

from app.api.profile import get_or_create_local_user
from app.db import SessionLocal
from app.messaging.base import Button
from app.messaging.service import MessagingService
from app.messaging.telegram import TelegramChannel, TelegramError
from app.models import CallbackToken, ChatMessage, LlmUsage, MessagingLink
from app.pipeline.state import is_paused, set_paused

TOKEN = "123456:SECRET-token-value-for-tests"


# --- Telegram client -------------------------------------------------------------------


def _client(handler) -> TelegramChannel:
    return TelegramChannel(TOKEN, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_send_text_with_buttons_builds_inline_keyboard() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    channel = _client(handler)
    message_id = await channel.send_text(
        "99",
        "Hello",
        [[Button("Apply", data="t:abc"), Button("Posting", url="https://x.test/job")]],
    )
    assert message_id == "42"
    assert seen["path"].endswith("/sendMessage")
    keyboard = seen["body"]["reply_markup"]["inline_keyboard"]
    assert keyboard == [
        [
            {"text": "Apply", "callback_data": "t:abc"},
            {"text": "Posting", "url": "https://x.test/job"},
        ]
    ]
    assert "parse_mode" not in seen["body"]


@pytest.mark.asyncio
async def test_send_document_is_multipart(tmp_path: Path) -> None:
    pdf = tmp_path / "cv.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content_type"] = request.headers["content-type"]
        seen["body"] = request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    channel = _client(handler)
    assert await channel.send_document("99", pdf, "Your CV", [[Button("Apply", data="t:x")]]) == "7"
    assert seen["content_type"].startswith("multipart/form-data")
    assert b"%PDF-1.4 test" in seen["body"]
    assert b"inline_keyboard" in seen["body"]


@pytest.mark.asyncio
async def test_errors_never_leak_the_token() -> None:
    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"ok": False, "description": "Unauthorized"})

    with pytest.raises(TelegramError) as info:
        await _client(failing).get_me()
    assert "Unauthorized" in str(info.value)
    assert TOKEN not in str(info.value)

    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url}")

    with pytest.raises(TelegramError) as info:
        await _client(offline).get_me()
    assert TOKEN not in str(info.value)


# --- router ------------------------------------------------------------------------------


class FakeChannel:
    name = "telegram"

    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.answers: list[tuple[str, str | None]] = []
        self.cleared: list[str] = []

    async def send_text(self, chat_id, text, buttons=None, *, html=False):
        self.sent.append({"chat_id": chat_id, "text": text, "buttons": buttons})
        return str(len(self.sent))

    async def send_document(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append(
            {"chat_id": chat_id, "text": caption, "document": path, "buttons": buttons}
        )
        return str(len(self.sent))

    async def send_photo(self, chat_id, path, caption="", buttons=None, *, html=False):
        self.sent.append({"chat_id": chat_id, "text": caption, "photo": path, "buttons": buttons})
        return str(len(self.sent))

    async def clear_buttons(self, chat_id, message_id):
        self.cleared.append(message_id)

    async def answer_callback(self, callback_id, text=None):
        self.answers.append((callback_id, text))

    async def aclose(self):
        pass


@pytest.fixture
def service():
    with SessionLocal() as session:
        session.execute(delete(MessagingLink))
        session.execute(delete(CallbackToken))
        session.commit()
    set_paused(False)
    svc = MessagingService()
    svc.channel = FakeChannel()
    return svc


def _user_id() -> int:
    with SessionLocal() as session:
        return get_or_create_local_user(session).id


def _message(text: str, chat: int = 500, sender: int = 500) -> dict:
    return {
        "message": {"message_id": 1, "chat": {"id": chat}, "from": {"id": sender}, "text": text}
    }


def _tap(data: str, chat: int = 500, sender: int = 500) -> dict:
    return {
        "callback_query": {
            "id": "cb1",
            "from": {"id": sender},
            "data": data,
            "message": {"message_id": 77, "chat": {"id": chat}},
        }
    }


async def _pair(service: MessagingService) -> int:
    user_id = _user_id()
    code = service.new_link_code(user_id)
    await service.router.handle_update(_message(f"/start {code}"))
    return user_id


@pytest.mark.asyncio
async def test_pairing_is_one_time_and_strangers_are_ignored(service) -> None:
    user_id = _user_id()
    code = service.new_link_code(user_id)
    await service.router.handle_update(_message(f"/start {code}"))
    assert service.linked(user_id).chat_id == "500"
    assert "Connected" in service.channel.sent[-1]["text"]

    # The same code can't pair a second chat.
    await service.router.handle_update(_message(f"/start {code}", chat=600, sender=600))
    assert service.linked(user_id).chat_id == "500"

    # Strangers get a polite refusal and nothing happens.
    set_paused(False)
    await service.router.handle_update(_message("/pause", chat=600, sender=600))
    assert not is_paused()
    assert service.channel.sent[-1]["chat_id"] == "600"
    assert "private" in service.channel.sent[-1]["text"]

    # Same chat, different sender (e.g. a group) is also refused.
    await service.router.handle_update(_message("/pause", chat=500, sender=999))
    assert not is_paused()


@pytest.mark.asyncio
async def test_expired_link_code_does_not_pair(service) -> None:
    user_id = _user_id()
    code = service.new_link_code(user_id)
    with SessionLocal() as session:
        link = session.query(MessagingLink).filter_by(user_id=user_id).one()
        link.link_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()
    await service.router.handle_update(_message(f"/start {code}"))
    assert service.linked(user_id) is None


@pytest.mark.asyncio
async def test_commands_status_pause_resume(service) -> None:
    await _pair(service)
    await service.router.handle_update(_message("/status"))
    assert "Active" in service.channel.sent[-1]["text"]
    await service.router.handle_update(_message("/pause"))
    assert is_paused()
    await service.router.handle_update(_message("/status"))
    assert "Paused" in service.channel.sent[-1]["text"]
    await service.router.handle_update(_message("/resume"))
    assert not is_paused()


@pytest.mark.asyncio
async def test_button_taps_are_single_use_and_group_scoped(service) -> None:
    user_id = await _pair(service)
    calls: list[tuple[str, int | None, dict]] = []

    async def handler(ctx):
        calls.append((ctx.token.action, ctx.token.target_id, ctx.token.payload))

    service.router.register_action("pick", handler)
    group = service.new_group()
    a = service.button(user_id, "A", "pick", target_id=1, payload={"v": "a"}, group=group)
    b = service.button(user_id, "B", "pick", target_id=1, payload={"v": "b"}, group=group)
    assert len(a.data) <= 64

    await service.router.handle_update(_tap(a.data))
    await service.router.handle_update(_tap(a.data))  # double tap
    await service.router.handle_update(_tap(b.data))  # sibling already spent
    assert calls == [("pick", 1, {"v": "a"})]
    assert service.channel.answers[-1][1] == "This button has expired."

    # A stranger can't use a valid token.
    c = service.button(user_id, "C", "pick", target_id=2)
    await service.router.handle_update(_tap(c.data, chat=600, sender=600))
    assert len(calls) == 1
    await service.router.handle_update(_tap(c.data))
    assert calls[-1][1] == 2


@pytest.mark.asyncio
async def test_replies_and_links_are_routed(service) -> None:
    user_id = await _pair(service)
    got: list[tuple[str, str]] = []

    async def on_reply(uid, text):
        got.append(("reply", text))

    async def on_url(uid, url):
        got.append(("url", url))

    service.router.set_url_handler(on_url)
    service.router.expect_reply(user_id, on_reply)
    await service.router.handle_update(_message("CA$140k"))
    await service.router.handle_update(_message("look at https://jobs.lever.co/acme/123."))
    assert got == [("reply", "CA$140k"), ("url", "https://jobs.lever.co/acme/123")]

    with SessionLocal() as session:
        kinds = [m.kind for m in session.query(ChatMessage).filter_by(user_id=user_id)]
    assert "text" in kinds


# --- spend ledger + budget ------------------------------------------------------------------


def test_usage_ledger_and_budget(monkeypatch) -> None:
    from app.config import get_settings
    from app.llm.usage import BudgetExceededError, check_budget, record_usage, spend_summary

    with SessionLocal() as session:
        session.execute(delete(LlmUsage))
        session.commit()
    usage = SimpleNamespace(
        input_tokens=1_000_000,
        output_tokens=0,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
        cache_creation=None,
        server_tool_use=SimpleNamespace(web_search_requests=3),
    )
    cost = record_usage("claude-haiku-5-5", usage, task="screen")
    assert cost == pytest.approx(0.10 + 0.03)
    assert spend_summary()["today_usd"] == pytest.approx(0.13)

    settings = get_settings()
    monkeypatch.setattr(settings, "daily_budget_usd", 0.10)
    with pytest.raises(BudgetExceededError):
        check_budget()
    monkeypatch.setattr(settings, "daily_budget_usd", 10.0)
    check_budget()


# --- Claude provider ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_provider_routes_task_model_and_records_usage(monkeypatch) -> None:
    from app.llm.anthropic_provider import AnthropicProvider
    from app.llm.base import ChatMessage as LlmMessage
    from app.llm.usage import llm_task

    captured: dict = {}

    class FakeMessages:
        async def create(self, **params):
            captured.update(params)
            return SimpleNamespace(
                content=[SimpleNamespace(type="text", text='{"score": 80}')],
                model=params["model"],
                stop_reason="end_turn",
                usage=SimpleNamespace(
                    input_tokens=100,
                    output_tokens=20,
                    cache_read_input_tokens=0,
                    cache_creation_input_tokens=0,
                    cache_creation=None,
                    server_tool_use=None,
                ),
                model_dump=lambda: {},
            )

    provider = AnthropicProvider(api_key="sk-test", default_model="claude-opus-5-5")
    monkeypatch.setattr(provider, "_client", SimpleNamespace(messages=FakeMessages()))
    with SessionLocal() as session:
        session.execute(delete(LlmUsage))
        session.commit()

    with llm_task("match"):
        resp = await provider.chat(
            [LlmMessage("system", "Score it."), LlmMessage("user", "Job...")],
            temperature=0.1,
            max_tokens=160,
            response_format={"type": "json_object"},
        )
    assert resp.content == '{"score": 80}'
    assert captured["model"] == "claude-haiku-5-5"
    assert "temperature" not in captured
    assert captured["max_tokens"] >= 4096
    assert "JSON object" in captured["system"]
    assert captured["messages"] == [{"role": "user", "content": "Job..."}]
    with SessionLocal() as session:
        row = session.query(LlmUsage).one()
    assert (row.task, row.model) == ("match", "claude-haiku-5-5")
