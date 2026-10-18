"""Ollama provider.

Talks to a local Ollama daemon (default `http://localhost:11434`) via its
native `/api/chat` endpoint. We deliberately don't go through Ollama's
OpenAI-compat shim so we can pass through Ollama-specific options later.
"""

from __future__ import annotations

from typing import Any

import httpx

from .base import ChatMessage, ChatResponse, LLMError, LLMProvider


def _extract_base64_image(url: str) -> str:
    """Strip a data-URI prefix so Ollama receives raw base64."""

    if url.startswith("data:"):
        _, _, data = url.partition(",")
        return data.strip()
    return url.strip()


def to_ollama_message(message: ChatMessage) -> dict[str, Any]:
    """Convert a ChatMessage to Ollama's native /api/chat shape.

    OpenAI-style multimodal parts (text + image_url) become a string
    ``content`` plus an optional ``images`` list — required for vision models
    such as Gemma 4 on Ollama.
    """

    content = message.content
    if isinstance(content, str):
        return {"role": message.role, "content": content}

    if not isinstance(content, list):
        return {"role": message.role, "content": str(content)}

    text_parts: list[str] = []
    images: list[str] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        part_type = part.get("type", "")
        if part_type == "text":
            text = part.get("text", "")
            if text:
                text_parts.append(str(text))
        elif part_type == "image_url":
            url = (part.get("image_url") or {}).get("url", "")
            if url:
                images.append(_extract_base64_image(str(url)))

    msg: dict[str, Any] = {
        "role": message.role,
        "content": "\n".join(text_parts) if text_parts else " ",
    }
    if images:
        msg["images"] = images
    return msg


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        default_model: str = "llama3.1",
        timeout_s: float = 7200.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_model = default_model
        self._timeout_s = timeout_s
        self._client = httpx.AsyncClient(timeout=timeout_s)

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
        options: dict[str, Any] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            # Ollama uses `num_predict` for max generation tokens.
            options["num_predict"] = max_tokens

        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [to_ollama_message(m) for m in messages],
            "stream": False,
        }
        if options:
            payload["options"] = options
        if response_format is not None and response_format.get("type") == "json_object":
            # Ollama supports forced JSON via `format: "json"`.
            payload["format"] = "json"
        if thinking is not None:
            payload["think"] = thinking

        url = f"{self.base_url}/api/chat"
        effective_timeout = timeout_s if timeout_s is not None else self._timeout_s

        try:
            resp = await self._client.post(url, json=payload, timeout=effective_timeout)
        except httpx.HTTPError as e:
            detail = str(e).strip() or type(e).__name__
            if isinstance(e, httpx.TimeoutException):
                detail = f"timed out after {effective_timeout:.0f}s"
            raise LLMError(
                f"HTTP error calling Ollama at {url}: {detail}. "
                "Is the Ollama daemon running?"
            ) from e

        if resp.status_code >= 400:
            raise LLMError(
                f"Ollama call failed ({resp.status_code}): {resp.text[:500]}"
            )

        data = resp.json()
        try:
            content = data["message"]["content"] or ""
        except (KeyError, TypeError) as e:
            raise LLMError(f"Unexpected Ollama response: {data}") from e

        return ChatResponse(
            content=content,
            model=data.get("model", payload["model"]),
            provider=self.name,
            raw=data,
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
        )

    async def aclose(self) -> None:
        await self._client.aclose()
