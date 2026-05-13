"""Tests test llm module."""

import pytest

from cafe.agents import llm
from cafe.config import Settings


class FakeModel:
    def __init__(self, **kwargs):
        """Initialize the instance.

        Args:
            - kwargs: Any - The kwargs value.

        Returns:
            - return None - The return value.
        """
        self.kwargs = kwargs


def test_openrouter_uses_openai_compatible_base_url(monkeypatch):
    """Verify openrouter uses openai compatible base url.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setattr(llm, "OpenAIChatModel", FakeModel)
    settings = Settings(
        _env_file=None,
        llm_provider="openrouter",
        openai_model="openai/gpt-4o-mini",
        openai_api_key="key",
    )

    model = llm.make_chat_model(settings)

    assert model.kwargs["model_name"] == "openai/gpt-4o-mini"
    assert model.kwargs["api_key"] == "key"
    assert model.kwargs["client_kwargs"] == {"base_url": "https://openrouter.ai/api/v1"}


def test_llm_base_url_overrides_provider_default(monkeypatch):
    """Verify llm base url overrides provider default.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setattr(llm, "OpenAIChatModel", FakeModel)
    settings = Settings(
        _env_file=None,
        llm_provider="groq",
        llm_base_url="https://gateway.example.test/v1",
        openai_model="llama-3.3-70b-versatile",
        openai_api_key="key",
    )

    model = llm.make_chat_model(settings)

    assert model.kwargs["client_kwargs"] == {
        "base_url": "https://gateway.example.test/v1"
    }


def test_google_provider_uses_gemini_model(monkeypatch):
    """Verify google provider uses gemini model.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setattr(llm, "GeminiChatModel", FakeModel)
    settings = Settings(
        _env_file=None,
        llm_provider="google",
        openai_model="gemini-2.0-flash",
        openai_api_key="key",
    )

    model = llm.make_chat_model(settings)

    assert model.kwargs["model_name"] == "gemini-2.0-flash"
    assert model.kwargs["api_key"] == "key"
    assert model.kwargs["stream"] is False


def test_ollama_provider_does_not_require_api_key(monkeypatch):
    """Verify ollama provider does not require api key.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setattr(llm, "OllamaChatModel", FakeModel)
    settings = Settings(
        _env_file=None,
        llm_provider="ollama",
        llm_base_url="http://localhost:11434",
        openai_model="llama3.2",
    )

    model = llm.make_chat_model(settings)

    assert model.kwargs["model_name"] == "llama3.2"
    assert model.kwargs["host"] == "http://localhost:11434"


def test_missing_api_key_has_provider_specific_message():
    """Verify missing api key has provider specific message.

    Returns:
        - return None - The return value.
    """
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        openai_model="claude-3-5-haiku-latest",
        openai_api_key="",
    )

    with pytest.raises(
        RuntimeError, match="LLM_API_KEY not set for provider 'anthropic'"
    ):
        llm.make_chat_model(settings)


def test_unsupported_provider_has_clear_message():
    """Verify unsupported provider has clear message.

    Returns:
        - return None - The return value.
    """
    settings = Settings(
        _env_file=None,
        llm_provider="made-up-provider",
        openai_model="some-model",
        openai_api_key="key",
    )

    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        llm.make_chat_model(settings)
