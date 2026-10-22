"""Typed application settings, loaded from environment or `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

LLMProviderName = Literal["anthropic", "openai", "ollama"]


class Settings(BaseSettings):
    """Application settings.

    Values come from (in order of precedence): real environment variables,
    `backend/.env`, then the defaults defined here.
    """

    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_env: Literal["dev", "prod", "test"] = "dev"
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    # --- Database ---
    database_url: str = "sqlite:///./data/jobbee.db"

    # --- Secrets (inbox tokens, ATS passwords) ---
    secret_store: Literal["keyring", "memory"] = Field(
        default="keyring",
        description="keyring = OS credential store (Windows Credential Manager); memory = tests only.",
    )
    secret_store_service: str = "JobBee"

    # --- LLM ---
    llm_provider: LLMProviderName = "anthropic"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field(default="", repr=False)
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_model: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    agentrouter_api_key: str = Field(default="", repr=False)
    agentrouter_base_url: str = "https://agentrouter.org/v1"
    llm_model: str = "gpt-4o-mini"
    llm_temperature: float = 0.2
    llm_timeout_s: float = Field(
        default=600.0,
        description="Default timeout for matching and quick LLM calls (seconds).",
    )
    llm_long_timeout_s: float = Field(
        default=7200.0,
        description="Timeout for long inputs such as resume extraction (seconds, max 2h).",
    )

    # --- Claude apply agent (browser toolset) ---
    anthropic_api_key: str = Field(default="", repr=False)
    anthropic_workspace_id: str = Field(
        default="",
        description="Workspace for organization-wide API keys (sent as anthropic-workspace-id).",
    )
    apply_model: str = "claude-opus-5-5"
    apply_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    apply_max_iterations: int = Field(default=150, ge=10, le=500)
    apply_max_cost_usd: float = Field(
        default=5.0,
        description="Per-application spend cap; the run stops when its estimated cost passes this.",
    )

    submit_ttl_min: int = Field(
        default=45,
        ge=5,
        description="How long a filled form waits for the user's Submit tap before it expires.",
    )
    max_parked_runs: int = Field(default=2, ge=1, le=5)

    # --- Claude models per task (used when LLM_PROVIDER=anthropic) ---
    match_model: str = "claude-haiku-5-5"
    import_model: str = "claude-haiku-5-5"
    tailor_model: str = "claude-sonnet-5-5"
    discover_model: str = "claude-sonnet-5-5"
    chat_model: str = "claude-haiku-5-5"

    # --- Spend limits across every Claude call (llm_usage ledger) ---
    daily_budget_usd: float = Field(default=10.0, ge=0)
    monthly_budget_usd: float = Field(default=100.0, ge=0)

    # --- Email sync ---
    email_enrich_jd: bool = Field(
        default=False,
        description="When true, fetch full job descriptions from posting URLs for short email snippets.",
    )

    # --- Gmail OAuth (installer configures once in .env — users only click Sign in with Google) ---
    gmail_client_id: str = ""
    gmail_client_secret: str = Field(default="", repr=False)
    gmail_oauth_redirect_uri: str = "http://127.0.0.1:8000/api/email/gmail/callback"
    frontend_url: str = "http://localhost:5173"

    def resolved_llm_api_key(self) -> str:
        """Pick the API key for the active provider."""

        if self.llm_provider == "anthropic":
            return self.anthropic_api_key.strip()

        base = (self.llm_base_url or "").lower()
        if "agentrouter" in base and self.agentrouter_api_key.strip():
            return self.agentrouter_api_key.strip()
        if self.openrouter_api_key.strip():
            return self.openrouter_api_key.strip()
        if self.agentrouter_api_key.strip() and not self.llm_api_key.strip():
            return self.agentrouter_api_key.strip()
        return self.llm_api_key.strip()

    def resolved_llm_base_url(self) -> str:
        """Normalize base URL for the active provider."""

        if self.llm_provider == "anthropic":
            return "https://api.anthropic.com"

        if self.llm_provider != "openai":
            return self.llm_base_url.rstrip("/")

        if "agentrouter" in self.llm_base_url.lower() and self.agentrouter_api_key.strip():
            url = (self.agentrouter_base_url or self.llm_base_url).strip().rstrip("/")
            if "agentrouter" in url.lower() and not url.endswith("/v1"):
                return f"{url}/v1"
            return url

        if self.openrouter_api_key.strip():
            return self.openrouter_base_url.strip().rstrip("/")

        return self.llm_base_url.strip().rstrip("/")

    def resolved_llm_model(self) -> str:
        """Return the configured model id."""

        if self.llm_provider == "anthropic":
            model = self.llm_model.strip()
            return model if model.startswith("claude-") else self.match_model
        if (
            self.llm_provider == "openai"
            and self.openrouter_api_key.strip()
            and self.openrouter_model.strip()
        ):
            return self.openrouter_model.strip()
        return self.llm_model.strip() or "gpt-4o-mini"

    @property
    def is_dev(self) -> bool:
        return self.app_env == "dev"

    def gmail_oauth_available(self) -> bool:
        """True when the installer configured OAuth credentials (never shown to end users)."""

        return bool(self.gmail_client_id.strip() and self.gmail_client_secret.strip())

    def resolved_database_url(self) -> str:
        """Resolve relative SQLite URLs against the backend directory."""

        url = self.database_url
        prefix = "sqlite:///"
        if url.startswith(prefix):
            tail = url[len(prefix):]
            path = Path(tail)
            if not path.is_absolute():
                path = (BACKEND_DIR / path).resolve()
                path.parent.mkdir(parents=True, exist_ok=True)
                return f"{prefix}{path.as_posix()}"
        return url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor so importing modules share one Settings instance."""

    return Settings()
