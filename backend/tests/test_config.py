"""Settings resolution tests."""

from __future__ import annotations

from app.config import Settings


def test_apply_vision_model_defaults_to_llm_model_for_ollama() -> None:
    settings = Settings(
        llm_provider="ollama",
        llm_model="gemma4",
        apply_vision_model="",
    )
    assert settings.resolved_apply_vision_model() == "gemma4"


def test_apply_vision_model_explicit_override() -> None:
    settings = Settings(
        llm_provider="ollama",
        llm_model="gemma4",
        apply_vision_model="llava",
    )
    assert settings.resolved_apply_vision_model() == "llava"


def test_apply_vision_model_openrouter_default() -> None:
    settings = Settings(
        llm_provider="openai",
        openrouter_api_key="sk-test",
        llm_model="google/gemma-4-31b-it:free",
        apply_vision_model="",
    )
    assert settings.resolved_apply_vision_model() == "qwen/qwen2.5-vl-32b-instruct"


def test_browser_use_key_supports_user_env_name() -> None:
    settings = Settings(
        apply_browser_engine="auto",
        browseruse_api_key="bu-test",
        browser_use_api_key="",
    )

    assert settings.resolved_browser_use_api_key() == "bu-test"
    assert settings.use_browser_use_apply() is True
    assert settings.use_browser_use_cloud_apply() is False


def test_apply_browser_engine_can_force_playwright() -> None:
    settings = Settings(apply_browser_engine="playwright", browseruse_api_key="bu-test")

    assert settings.use_browser_use_apply() is False


def test_browser_use_cloud_requires_explicit_engine() -> None:
    settings = Settings(apply_browser_engine="browser_use_cloud", browseruse_api_key="bu-test")

    assert settings.use_browser_use_apply() is True
    assert settings.use_browser_use_cloud_apply() is True


def test_agentrouter_base_url_wins_over_openrouter_key() -> None:
    settings = Settings(
        llm_provider="openai",
        llm_base_url="https://agentrouter.org/v1",
        agentrouter_api_key="agent-key",
        openrouter_api_key="openrouter-key",
    )

    assert settings.resolved_llm_base_url() == "https://agentrouter.org/v1"
    assert settings.resolved_llm_api_key() == "agent-key"


def test_openrouter_model_does_not_override_ollama_model() -> None:
    settings = Settings(
        llm_provider="ollama",
        llm_model="gemma4-e4b-agent:latest",
        openrouter_api_key="sk-test",
        openrouter_model="nex-agi/nex-n2-pro:free",
    )

    assert settings.resolved_llm_model() == "gemma4-e4b-agent:latest"


def test_browser_use_defaults_avoid_long_unsettled_loops() -> None:
    settings = Settings()

    assert settings.browser_use_max_steps == 30
    assert settings.browser_use_wait_before_model_s == 2
    assert settings.browser_use_min_page_load_wait_s == 2
    assert settings.browser_use_network_idle_wait_s == 2
    assert settings.browser_use_wait_between_actions_s == 1
    assert settings.browser_use_window_width == 0
    assert settings.browser_use_window_height == 0
    assert settings.browser_use_workspace_id == ""
    assert settings.browser_use_allow_workspace_create is False
