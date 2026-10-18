"""Typed application settings, loaded from environment or `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent

LLMProviderName = Literal["openai", "ollama"]
ApplyBrowserEngine = Literal["auto", "playwright", "browser_use", "browser_use_cloud"]


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

    # --- LLM ---
    llm_provider: LLMProviderName = "openai"
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

    # --- Apply vision (multimodal LLM reads screenshots to navigate application UIs) ---
    apply_vision_enabled: bool = Field(
        default=True,
        description="Use a multimodal browser agent to navigate application UIs.",
    )
    apply_vision_model: str = Field(
        default="",
        description=(
            "Vision model id. Empty falls back to LLM_MODEL (Ollama/local) or "
            "qwen2.5-vl on OpenRouter."
        ),
    )
    apply_harness_max_steps: int = Field(
        default=24,
        description="Max observe-plan-act steps during application form preparation.",
    )
    apply_harness_stuck_threshold: int = Field(
        default=3,
        description="Identical consecutive screenshots before forced Escape recovery.",
    )
    apply_harness_save_session: bool = Field(
        default=True,
        description="Persist per-step screenshots and action logs under data/screenshots/sessions/.",
    )
    apply_browser_engine: ApplyBrowserEngine = Field(
        default="auto",
        description=(
            "Browser automation engine for application forms. 'auto' prefers local/open-source "
            "Browser Use when installed, otherwise the local Playwright harness. Cloud is only "
            "used when explicitly set to 'browser_use_cloud'."
        ),
    )
    browseruse_api_key: str = Field(
        default="",
        repr=False,
        description="Browser Use API key from BROWSERUSE_API_KEY.",
    )
    browser_use_api_key: str = Field(
        default="",
        repr=False,
        description="Browser Use API key from BROWSER_USE_API_KEY.",
    )
    browser_use_model: str = Field(
        default="",
        description=(
            "Optional model override for Browser Use apply automation. Local mode accepts "
            "LiteLLM model names; cloud mode accepts Browser Use Cloud model names."
        ),
    )
    browser_use_max_steps: int = Field(
        default=30,
        description="Max local Browser Use agent steps during application form preparation.",
    )
    browser_use_timeout_s: float = Field(
        default=1800.0,
        description="Max Browser Use apply-agent runtime in seconds.",
    )
    browser_use_step_timeout_s: float = Field(
        default=90.0,
        description="Max seconds for one local Browser Use reasoning/action step.",
    )
    browser_use_max_failures: int = Field(
        default=2,
        description="Consecutive Browser Use local step failures before stopping.",
    )
    browser_use_wait_before_model_s: float = Field(
        default=2.0,
        description="Seconds to let the browser settle before each Browser Use model step.",
    )
    browser_use_min_page_load_wait_s: float = Field(
        default=2.0,
        description="Minimum page-load wait configured on the local Browser Use browser profile.",
    )
    browser_use_network_idle_wait_s: float = Field(
        default=2.0,
        description="Network-idle wait configured on the local Browser Use browser profile.",
    )
    browser_use_wait_between_actions_s: float = Field(
        default=1.0,
        description="Delay between Browser Use browser actions.",
    )
    browser_use_window_width: int = Field(
        default=0,
        description="Optional local Browser Use window width. 0 uses the detected screen width.",
    )
    browser_use_window_height: int = Field(
        default=0,
        description="Optional local Browser Use window height. 0 uses the detected screen height.",
    )
    browser_use_human_login_wait_s: float = Field(
        default=600.0,
        description="How long Browser Use should wait for human login in the live browser.",
    )
    browser_use_auto_open_live: bool = Field(
        default=True,
        description="In dev, automatically open Browser Use live browser sessions for human login/review.",
    )
    browser_use_proxy_country_code: str = Field(
        default="",
        description="Optional Browser Use proxy country code. Blank uses Browser Use defaults.",
    )
    browser_use_workspace_id: str = Field(
        default="",
        description=(
            "Browser Use Cloud workspace id to reuse for apply automation. "
            "Blank reuses the first existing workspace returned by Browser Use."
        ),
    )
    browser_use_allow_workspace_create: bool = Field(
        default=False,
        description=(
            "Allow Browser Use Cloud to create a workspace when none exists. "
            "Keep false to avoid subscription/quota errors from accidental workspace creation."
        ),
    )

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
        """Pick the API key for the active OpenAI-compatible endpoint."""

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

        if (
            self.llm_provider == "openai"
            and self.openrouter_api_key.strip()
            and self.openrouter_model.strip()
        ):
            return self.openrouter_model.strip()
        return self.llm_model.strip() or "gpt-4o-mini"

    def resolved_apply_vision_model(self) -> str:
        """Vision model for apply-agent screenshot decisions."""

        if self.apply_vision_model.strip():
            return self.apply_vision_model.strip()
        if self.llm_provider == "ollama":
            return self.resolved_llm_model()
        if self.openrouter_api_key.strip():
            return "qwen/qwen2.5-vl-32b-instruct"
        return self.resolved_llm_model()

    def resolved_browser_use_api_key(self) -> str:
        """Return the Browser Use API key, accepting both env var spellings."""

        return (self.browser_use_api_key or self.browseruse_api_key).strip()

    def use_browser_use_apply(self) -> bool:
        """True when the apply flow should use Browser Use as its primary engine."""

        if self.apply_browser_engine in ("browser_use", "browser_use_cloud"):
            return True
        if self.apply_browser_engine == "playwright":
            return False
        return True

    def use_browser_use_cloud_apply(self) -> bool:
        """True only when the user explicitly opted into Browser Use Cloud."""

        return self.apply_browser_engine == "browser_use_cloud"

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
