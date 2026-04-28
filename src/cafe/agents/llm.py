"""Provider-aware LLM construction for cafe agents."""

from agentscope.model import (
    AnthropicChatModel,
    DashScopeChatModel,
    GeminiChatModel,
    OllamaChatModel,
    OpenAIChatModel,
)

from cafe.config import Settings, get_settings


_PROVIDER_ALIASES = {
    "google": "gemini",
    "google-gemini": "gemini",
    "openai-compatible": "openai",
}

_OPENAI_COMPATIBLE_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
}


def normalized_provider(settings: Settings | None = None) -> str:
    s = settings or get_settings()
    provider = s.llm_provider.strip().lower()
    return _PROVIDER_ALIASES.get(provider, provider)


def configured_model_name(settings: Settings | None = None) -> str:
    return (settings or get_settings()).openai_model


def _require_api_key(settings: Settings, provider: str) -> str:
    if not settings.openai_api_key:
        raise RuntimeError(
            f"LLM_API_KEY not set for provider '{provider}'. "
            "Set LLM_API_KEY in .env, or use the provider-specific API key name.",
        )
    return settings.openai_api_key


def make_chat_model(settings: Settings | None = None):
    """Create the chat model configured by LLM_PROVIDER/LLM_MODEL/LLM_API_KEY."""

    s = settings or get_settings()
    provider = normalized_provider(s)
    model_name = configured_model_name(s)
    base_url = s.llm_base_url.strip() or _OPENAI_COMPATIBLE_BASE_URLS.get(provider, "")

    if provider in {"openai", "deepseek", "groq", "openrouter"}:
        client_kwargs = {"base_url": base_url} if base_url else None
        return OpenAIChatModel(
            model_name=model_name,
            api_key=_require_api_key(s, provider),
            stream=False,
            client_kwargs=client_kwargs,
        )

    if provider == "anthropic":
        return AnthropicChatModel(
            model_name=model_name,
            api_key=_require_api_key(s, provider),
            stream=False,
        )

    if provider == "gemini":
        return GeminiChatModel(
            model_name=model_name,
            api_key=_require_api_key(s, provider),
            stream=False,
        )

    if provider == "ollama":
        return OllamaChatModel(
            model_name=model_name,
            stream=False,
            host=base_url or None,
        )

    if provider == "dashscope":
        return DashScopeChatModel(
            model_name=model_name,
            api_key=_require_api_key(s, provider),
            stream=False,
            base_http_api_url=base_url or None,
        )

    supported = "openai, deepseek, groq, openrouter, anthropic, google/gemini, ollama, dashscope"
    raise RuntimeError(f"Unsupported LLM_PROVIDER '{s.llm_provider}'. Supported providers: {supported}.")
