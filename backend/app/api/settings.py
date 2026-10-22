"""Read and update LLM settings at runtime.

Updates are persisted to `backend/.env` so they survive restarts. We then
clear the cached `Settings` and `LLMProvider` so subsequent calls pick up
the new values.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..config import BACKEND_DIR, get_settings
from ..llm.factory import reset_llm_provider
from ..schemas import SettingsResponse, SettingsUpdate
from ..services.secrets import ANTHROPIC_API_KEY, claude_api_key, get_secret_store

router = APIRouter(prefix="/settings", tags=["settings"])

ENV_PATH = BACKEND_DIR / ".env"

# Map Pydantic field names to .env keys (uppercase, same name).
_FIELD_TO_ENV = {
    "llm_provider": "LLM_PROVIDER",
    "llm_base_url": "LLM_BASE_URL",
    "llm_api_key": "LLM_API_KEY",
    "llm_model": "LLM_MODEL",
    "llm_temperature": "LLM_TEMPERATURE",
    "llm_timeout_s": "LLM_TIMEOUT_S",
}


def _read_env_lines() -> list[str]:
    if not ENV_PATH.exists():
        return []
    return ENV_PATH.read_text(encoding="utf-8").splitlines()


def _write_env(updates: dict[str, str]) -> None:
    """Upsert keys into `backend/.env`, preserving existing comments/order."""

    lines = _read_env_lines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            out.append(line)
            continue
        key, _, _ = stripped.partition("=")
        key = key.strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)

    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def _refresh_settings() -> None:
    """Drop cached settings + LLM singleton so the next request rebuilds them."""

    get_settings.cache_clear()
    reset_llm_provider()


def save_env(updates: dict[str, str]) -> None:
    """Persist settings to `backend/.env` and reload them."""

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    _write_env(updates)
    _refresh_settings()


def _to_response(s) -> SettingsResponse:
    return SettingsResponse(
        llm_provider=s.llm_provider,
        llm_base_url=s.llm_base_url,
        llm_model=s.resolved_llm_model() if s.llm_provider == "anthropic" else s.llm_model,
        llm_temperature=s.llm_temperature,
        llm_timeout_s=s.llm_timeout_s,
        has_api_key=bool(
            claude_api_key() if s.llm_provider == "anthropic" else s.resolved_llm_api_key()
        ),
    )


@router.get("", response_model=SettingsResponse)
def read_settings() -> SettingsResponse:
    return _to_response(get_settings())


@router.put("", response_model=SettingsResponse)
def update_settings(payload: SettingsUpdate) -> SettingsResponse:
    updates: dict[str, str] = {}
    data = payload.model_dump(exclude_unset=True)
    provider = data.get("llm_provider") or get_settings().llm_provider
    for field, value in data.items():
        env_key = _FIELD_TO_ENV.get(field)
        if env_key is None or value is None:
            continue
        if field == "llm_api_key" and provider == "anthropic":
            # Claude keys live in the OS keyring (see /api/setup/claude-key), never in .env.
            if str(value).strip():
                get_secret_store().set(ANTHROPIC_API_KEY, str(value).strip())
                reset_llm_provider()
            continue
        updates[env_key] = str(value)

    if updates:
        save_env(updates)

    return _to_response(get_settings())
