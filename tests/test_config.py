from cafe.config import Settings


def test_settings_accept_openai_env_names(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.openai_api_key == "openai-key"
    assert settings.openai_model == "gpt-4o-mini"


def test_settings_accept_llm_env_names(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    monkeypatch.setenv("LLM_API_KEY", "llm-key")
    monkeypatch.setenv("LLM_MODEL", "gpt-4.1-nano")
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setenv("EMBEDDING_MODEL_NAME", "BAAI/bge-small-en-v1.5")

    settings = Settings(_env_file=None)

    assert settings.openai_api_key == "llm-key"
    assert settings.openai_model == "gpt-4.1-nano"
    assert settings.embedding_model == "BAAI/bge-small-en-v1.5"


def test_settings_accept_provider_and_base_url(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openrouter"
    assert settings.llm_base_url == "https://example.test/v1"
