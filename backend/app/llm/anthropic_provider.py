"""Claude provider for the generic `LLMProvider` seam (import, matching, tailoring).

The browser apply agent talks to the SDK directly (app/apply/agent.py); this
provider covers the plain chat-style calls. Current Claude models reject
sampling parameters and always think adaptively, so `temperature` is ignored
and `thinking` maps to the effort level.
"""

from __future__ import annotations

from typing import Any

from anthropic import APIStatusError, APITimeoutError, AsyncAnthropic

from ..config import get_settings
from .base import ChatMessage, ChatResponse, LLMError, LLMProvider
from .usage import check_budget, current_task, record_usage

_JSON_INSTRUCTION = (
    "Respond with exactly one JSON object and nothing else: no prose, no code fences."
)
_MIN_MAX_TOKENS = 4096  # adaptive thinking shares the output budget


def task_model(task: str) -> str | None:
    """The model configured for a labelled task (see `llm_task`), if any."""

    settings = get_settings()
    return {
        "match": settings.match_model,
        "screen": settings.match_model,
        "import": settings.import_model,
        "tailor": settings.tailor_model,
        "discover": settings.discover_model,
        "fact_check": settings.match_model,
        "chat": settings.chat_model,
    }.get(task)


def _content_blocks(content: str | list[dict[str, Any]]) -> str | list[dict[str, Any]]:
    """Convert OpenAI-style multimodal parts to Anthropic content blocks."""

    if isinstance(content, str):
        return content
    blocks: list[dict[str, Any]] = []
    for part in content:
        if part.get("type") == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif part.get("type") == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url.startswith("data:") and ";base64," in url:
                media_type, data = url[5:].split(";base64,", 1)
                blocks.append(
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": data},
                    }
                )
    return blocks


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        timeout_s: float = 600.0,
        workspace_id: str = "",
    ) -> None:
        self.default_model = default_model
        self._api_key = api_key
        self._client = AsyncAnthropic(
            api_key=api_key or None,
            base_url="https://api.anthropic.com",
            timeout=timeout_s,
            default_headers={"anthropic-workspace-id": workspace_id} if workspace_id else None,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> ChatResponse:
        check_budget()
        model_id = model or task_model(current_task()) or self.default_model
        system_parts = [m.content for m in messages if m.role == "system"]
        if response_format and response_format.get("type") == "json_object":
            system_parts.append(_JSON_INSTRUCTION)
        turns = [
            {"role": m.role, "content": _content_blocks(m.content)}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        params: dict[str, Any] = {
            "model": model_id,
            "max_tokens": max(max_tokens or 0, _MIN_MAX_TOKENS),
            "messages": turns,
            "output_config": {"effort": "medium" if thinking else "low"},
        }
        if system_parts:
            params["system"] = "\n\n".join(str(p) for p in system_parts)
        client = self._client.with_options(timeout=timeout_s) if timeout_s else self._client
        try:
            response = await client.messages.create(**params)
        except APITimeoutError as e:
            raise LLMError(f"Claude timed out: {e}") from e
        except APIStatusError as e:
            body = e.body if isinstance(e.body, dict) else {}
            detail = body.get("error", {}) if isinstance(body.get("error"), dict) else {}
            raise LLMError(
                f"Claude error {e.status_code}: {detail.get('message') or e.message}"
            ) from e

        record_usage(model_id, response.usage)
        if response.stop_reason == "refusal":
            raise LLMError("Claude declined this request.")
        text = "".join(block.text for block in response.content if block.type == "text")
        return ChatResponse(
            content=text.strip(),
            model=response.model,
            provider=self.name,
            raw=response.model_dump(),
            prompt_tokens=response.usage.input_tokens,
            completion_tokens=response.usage.output_tokens,
        )

    async def aclose(self) -> None:
        await self._client.close()
