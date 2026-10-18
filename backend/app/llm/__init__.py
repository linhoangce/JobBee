"""LLM abstraction layer.

One interface (`LLMProvider`) backed by multiple concrete providers. Agents
import the abstract types from here so they don't depend on a specific vendor.
"""

from .base import ChatMessage, ChatResponse, LLMError, LLMProvider
from .factory import build_provider, get_llm_provider

__all__ = [
    "ChatMessage",
    "ChatResponse",
    "LLMError",
    "LLMProvider",
    "build_provider",
    "get_llm_provider",
]
