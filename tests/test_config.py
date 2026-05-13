"""Tests test config module."""

from cafe.agents.memory import _normalize_async_database_url
from cafe.config import Settings


def test_settings_accept_openai_env_names(monkeypatch):
    """Verify settings accept openai env names.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4o-mini")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)

    settings = Settings(_env_file=None)

    assert settings.openai_api_key == "openai-key"
    assert settings.openai_model == "gpt-4o-mini"


def test_settings_accept_llm_env_names(monkeypatch):
    """Verify settings accept llm env names.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
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
    """Verify settings accept provider and base url.

    Args:
        - monkeypatch: Any - The monkeypatch value.

    Returns:
        - return None - The return value.
    """
    monkeypatch.setenv("LLM_PROVIDER", "openrouter")
    monkeypatch.setenv("LLM_BASE_URL", "https://example.test/v1")

    settings = Settings(_env_file=None)

    assert settings.llm_provider == "openrouter"
    assert settings.llm_base_url == "https://example.test/v1"


def test_neon_asyncpg_sslmode_url_is_normalized():
    """Verify neon asyncpg sslmode url is normalized.

    Returns:
        - return None - The return value.
    """
    url, kwargs = _normalize_async_database_url(
        "postgresql+asyncpg://user:pass@example.neon.tech/db?sslmode=require"
    )

    assert "sslmode" not in url
    assert url.startswith("postgresql+asyncpg://")
    assert kwargs == {"connect_args": {"ssl": True, "timeout": 15}}
