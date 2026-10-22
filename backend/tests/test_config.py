"""Settings resolution tests."""

from __future__ import annotations

from app.config import Settings


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


def test_claude_is_the_default_provider() -> None:
    settings = Settings(_env_file=None, llm_provider="anthropic", llm_model="")
    assert settings.resolved_llm_base_url() == "https://api.anthropic.com"
    assert settings.resolved_llm_model() == settings.match_model
