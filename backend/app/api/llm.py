"""LLM endpoints: connectivity test + simple chat passthrough."""

from __future__ import annotations

from fastapi import APIRouter

from ..config import get_settings
from ..llm.base import ChatMessage, LLMError, strip_model_artifacts
from ..llm.factory import build_provider, get_llm_provider
from ..schemas import LLMTestRequest, LLMTestResponse

router = APIRouter(prefix="/llm", tags=["llm"])


@router.post("/test", response_model=LLMTestResponse)
async def llm_test(req: LLMTestRequest) -> LLMTestResponse:
    """Send a tiny prompt to validate provider config.

    Any of `provider/base_url/api_key/model` may be supplied to test a
    pending change without saving it. Anything omitted falls back to the
    persisted settings.
    """

    settings = get_settings()
    provider_name = req.provider or settings.llm_provider
    base_url = req.base_url or settings.resolved_llm_base_url()
    api_key = req.api_key if req.api_key is not None else settings.resolved_llm_api_key()
    model = req.model or settings.resolved_llm_model()

    provider = build_provider(
        provider=provider_name,
        base_url=base_url,
        api_key=api_key,
        model=model,
        timeout_s=settings.llm_timeout_s,
    )

    try:
        resp = await provider.chat(
            [ChatMessage(role="user", content=req.prompt)],
            temperature=0.0,
            max_tokens=32,
            thinking=False,
        )
        return LLMTestResponse(
            ok=True,
            provider=resp.provider,
            model=resp.model,
            content=strip_model_artifacts(resp.content),
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )
    except LLMError as e:
        return LLMTestResponse(
            ok=False,
            provider=provider_name,
            model=model,
            error=str(e),
        )
    finally:
        await provider.aclose()


@router.post("/ping", response_model=LLMTestResponse)
async def llm_ping() -> LLMTestResponse:
    """Convenience: ping the currently-configured provider."""

    settings = get_settings()
    provider = get_llm_provider()
    try:
        resp = await provider.chat(
            [
                ChatMessage(
                    role="system",
                    content="Reply with plain text only. No thinking tags or reasoning blocks.",
                ),
                ChatMessage(role="user", content="Reply with exactly one word: pong"),
            ],
            temperature=0.0,
            max_tokens=64,
            thinking=False,
            timeout_s=settings.llm_timeout_s,
        )
        content = strip_model_artifacts(resp.content)
        return LLMTestResponse(
            ok=True,
            provider=resp.provider,
            model=resp.model,
            content=content,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )
    except LLMError as e:
        return LLMTestResponse(
            ok=False,
            provider=settings.llm_provider,
            model=settings.resolved_llm_model(),
            error=str(e),
        )
