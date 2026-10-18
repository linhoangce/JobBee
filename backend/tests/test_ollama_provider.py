"""Ollama provider tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.llm.base import ChatMessage
from app.llm.ollama import OllamaProvider, to_ollama_message


def test_to_ollama_message_string_content() -> None:
    msg = to_ollama_message(ChatMessage(role="system", content="You are helpful."))
    assert msg == {"role": "system", "content": "You are helpful."}


def test_to_ollama_message_openai_vision_parts() -> None:
    msg = to_ollama_message(
        ChatMessage(
            role="user",
            content=[
                {"type": "text", "text": "What should I click?"},
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/png;base64,abc123"},
                },
            ],
        )
    )
    assert msg["role"] == "user"
    assert msg["content"] == "What should I click?"
    assert msg["images"] == ["abc123"]


@pytest.mark.asyncio
async def test_ollama_chat_sends_native_vision_payload() -> None:
    provider = OllamaProvider(base_url="http://localhost:11434", default_model="gemma4")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "model": "gemma4",
        "message": {"role": "assistant", "content": '{"action":"scroll"}'},
    }

    with patch.object(provider._client, "post", AsyncMock(return_value=mock_resp)) as post:
        await provider.chat(
            [
                ChatMessage(role="system", content="Return JSON."),
                ChatMessage(
                    role="user",
                    content=[
                        {"type": "text", "text": "Next action?"},
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/png;base64,imgdata"},
                        },
                    ],
                ),
            ],
            model="gemma4",
            response_format={"type": "json_object"},
        )

    payload = post.await_args.kwargs["json"]
    assert payload["model"] == "gemma4"
    assert payload["format"] == "json"
    user_msg = payload["messages"][1]
    assert user_msg["content"] == "Next action?"
    assert user_msg["images"] == ["imgdata"]
    assert "image_url" not in str(user_msg)
