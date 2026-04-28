import pytest

from cafe.agents import llm
from cafe.config import Settings


class FakeModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_openrouter_uses_openai_compatible_base_url(monkeypatch):
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
    monkeypatch.setattr(llm, "OpenAIChatModel", FakeModel)
    settings = Settings(
        _env_file=None,
        llm_provider="groq",
        llm_base_url="https://gateway.example.test/v1",
        openai_model="llama-3.3-70b-versatile",
        openai_api_key="key",
    )

    model = llm.make_chat_model(settings)

    assert model.kwargs["client_kwargs"] == {"base_url": "https://gateway.example.test/v1"}


def test_google_provider_uses_gemini_model(monkeypatch):
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
    settings = Settings(
        _env_file=None,
        llm_provider="anthropic",
        openai_model="claude-3-5-haiku-latest",
        openai_api_key="",
    )

    with pytest.raises(RuntimeError, match="LLM_API_KEY not set for provider 'anthropic'"):
        llm.make_chat_model(settings)


def test_unsupported_provider_has_clear_message():
    settings = Settings(
        _env_file=None,
        llm_provider="made-up-provider",
        openai_model="some-model",
        openai_api_key="key",
    )

    with pytest.raises(RuntimeError, match="Unsupported LLM_PROVIDER"):
        llm.make_chat_model(settings)
