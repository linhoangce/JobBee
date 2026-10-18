"""Common types and the abstract `LLMProvider` interface."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

Role = Literal["system", "user", "assistant", "tool"]

# Gemma / reasoning models may emit thinking blocks before (or instead of) the answer.
_THINKING_RE = re.compile(r"<thought>[\s\S]*?</thought>", re.IGNORECASE)
# Some models use markdown-style thinking headers.
_THINKING_HEADER_RE = re.compile(r"^thinking\s*:?\s*", re.IGNORECASE | re.MULTILINE)


def strip_model_artifacts(text: str) -> str:
    """Remove provider-specific wrappers (e.g. Gemma thinking blocks) from text.

    JSON payloads wrapped entirely in ``<thought>`` are preserved — some models
    place the structured answer inside a thinking block.
    """

    if not text:
        return ""

    stripped = text.strip()
    thought_json = re.search(r"<thought>\s*(\{[\s\S]*)\s*</thought>", stripped, re.IGNORECASE)
    if thought_json:
        return thought_json.group(1).strip()

    open_thought = re.search(r"<thought>\s*(\{[\s\S]*)", stripped, re.IGNORECASE)
    if open_thought:
        return open_thought.group(1).strip()

    stripped = _THINKING_RE.sub("", stripped)
    stripped = _THINKING_HEADER_RE.sub("", stripped)
    return stripped.strip()


@dataclass(slots=True)
class ChatMessage:
    role: Role
    content: str | list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}


@dataclass(slots=True)
class ChatResponse:
    """Vendor-neutral chat result."""

    content: str
    model: str
    provider: str
    raw: dict[str, Any]
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class LLMError(RuntimeError):
    """Raised for any provider-side failure (HTTP error, timeout, bad payload)."""


class LLMProvider(ABC):
    """The single seam between agents and any concrete LLM backend."""

    name: str
    default_model: str

    @abstractmethod
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
        """Send a chat completion request and return the response."""

    async def aclose(self) -> None:
        """Release any underlying resources (HTTP clients, etc.)."""
