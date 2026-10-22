"""Provider factory.

`get_llm_provider()` returns a per-process singleton built from current
settings. `build_provider()` builds a fresh one from explicit args, used by
the `/api/llm/test` endpoint so the user can validate config edits before
saving them.
"""

from __future__ import annotations

from threading import Lock

from ..config import Settings, get_settings
from .anthropic_provider import AnthropicProvider
from .base import LLMError, LLMProvider
from .ollama import OllamaProvider
from .openai_compat import OpenAICompatProvider

_singleton_lock = Lock()
_singleton: LLMProvider | None = None
_singleton_signature: tuple | None = None


def build_provider(
    *,
    provider: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout_s: float = 60.0,
    long_timeout_s: float | None = None,
    workspace_id: str = "",
) -> LLMProvider:
    """Construct a provider from explicit values (used for /llm/test)."""

    p = provider.lower().strip()
    if p == "anthropic":
        return AnthropicProvider(
            api_key=api_key,
            default_model=model,
            timeout_s=long_timeout_s or timeout_s,
            workspace_id=workspace_id,
        )
    if p == "openai":
        return OpenAICompatProvider(
            base_url=base_url,
            api_key=api_key,
            default_model=model,
            timeout_s=timeout_s,
        )
    if p == "ollama":
        return OllamaProvider(
            base_url=base_url,
            default_model=model,
            timeout_s=long_timeout_s or timeout_s,
        )
    raise LLMError(f"Unknown LLM provider: {provider!r}")


def _api_key(s: Settings) -> str:
    if s.llm_provider == "anthropic":
        from ..services.secrets import claude_api_key

        return claude_api_key()
    return s.resolved_llm_api_key()


def _workspace_id(s: Settings) -> str:
    if s.llm_provider == "anthropic":
        from ..services.secrets import claude_workspace_id

        return claude_workspace_id()
    return ""


def _signature(s: Settings) -> tuple:
    return (
        s.llm_provider,
        s.resolved_llm_base_url(),
        _api_key(s),
        s.resolved_llm_model(),
        s.llm_timeout_s,
        s.llm_long_timeout_s,
        _workspace_id(s),
    )


def get_llm_provider() -> LLMProvider:
    """Return the configured singleton provider, rebuilding if settings changed."""

    global _singleton, _singleton_signature
    settings = get_settings()
    sig = _signature(settings)
    with _singleton_lock:
        if _singleton is None or sig != _singleton_signature:
            _singleton = build_provider(
                provider=settings.llm_provider,
                base_url=settings.resolved_llm_base_url(),
                api_key=sig[2],
                workspace_id=sig[6],
                model=settings.resolved_llm_model(),
                timeout_s=settings.llm_timeout_s,
                long_timeout_s=settings.llm_long_timeout_s,
            )
            _singleton_signature = sig
        return _singleton


def reset_llm_provider() -> None:
    """Drop the cached singleton; next `get_llm_provider()` call rebuilds it."""

    global _singleton, _singleton_signature
    with _singleton_lock:
        _singleton = None
        _singleton_signature = None
