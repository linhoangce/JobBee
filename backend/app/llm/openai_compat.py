"""OpenAI-compatible provider.

Works against any endpoint that implements `POST /chat/completions` with the
OpenAI request/response shape: OpenAI itself, OpenRouter, Groq, Together,
Gemini's OpenAI-compat layer, etc.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

import httpx

from .base import ChatMessage, ChatResponse, LLMError, LLMProvider

_RETRY_IN_RE = re.compile(r"retry in ([\d.]+)s", re.IGNORECASE)
_MAX_429_RETRIES = 3


class OpenAICompatProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        default_model: str,
        timeout_s: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
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
        if not self.api_key:
            raise LLMError(
                "LLM_API_KEY is empty. Set it in backend/.env or via the Settings page."
            )

        payload: dict[str, Any] = {
            "model": model or self.default_model,
            "messages": [m.to_dict() for m in messages],
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if response_format is not None:
            payload["response_format"] = response_format

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if "openrouter.ai" in self.base_url:
            headers["HTTP-Referer"] = "http://localhost:5173"
            headers["X-Title"] = "JobBee"
        if "agentrouter" in self.base_url.lower():
            headers["HTTP-Referer"] = "http://localhost:5173"
            headers["X-Title"] = "JobBee"

        last_error: LLMError | None = None
        effective_timeout = timeout_s if timeout_s is not None else self._timeout_s
        for attempt in range(_MAX_429_RETRIES):
            try:
                resp = await self._client.post(
                    url, json=payload, headers=headers, timeout=effective_timeout
                )
            except httpx.HTTPError as e:
                raise LLMError(f"HTTP error calling {url}: {e}") from e

            if resp.status_code == 429 and attempt + 1 < _MAX_429_RETRIES:
                wait_s = 2.0
                match = _RETRY_IN_RE.search(resp.text)
                if match:
                    wait_s = max(float(match.group(1)), 1.0)
                await asyncio.sleep(wait_s)
                continue

            if resp.status_code >= 400:
                last_error = LLMError(
                    f"OpenAI-compat call failed ({resp.status_code}): {resp.text[:500]}"
                )
                break

            data = resp.json()
            try:
                content = data["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, TypeError) as e:
                raise LLMError(f"Unexpected response shape: {data}") from e

            usage = data.get("usage") or {}
            return ChatResponse(
                content=content,
                model=data.get("model", payload["model"]),
                provider=self.name,
                raw=data,
                prompt_tokens=usage.get("prompt_tokens"),
                completion_tokens=usage.get("completion_tokens"),
            )

        raise last_error or LLMError("OpenAI-compat call failed after retries.")

    async def aclose(self) -> None:
        await self._client.aclose()
