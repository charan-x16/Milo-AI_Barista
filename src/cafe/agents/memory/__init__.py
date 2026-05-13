"""Public memory API for AgentScope agents.

Storage/schema helpers live in ``storage.py``. This package initializer keeps
agent-facing formatter and compression wiring separate from SQL persistence.
"""

from __future__ import annotations

from agentscope.agent import ReActAgent
from agentscope.formatter import (
    AnthropicChatFormatter,
    AnthropicMultiAgentFormatter,
    DashScopeChatFormatter,
    DashScopeMultiAgentFormatter,
    DeepSeekChatFormatter,
    DeepSeekMultiAgentFormatter,
    GeminiChatFormatter,
    GeminiMultiAgentFormatter,
    OllamaChatFormatter,
    OllamaMultiAgentFormatter,
    OpenAIChatFormatter,
    OpenAIMultiAgentFormatter,
)
from agentscope.token import CharTokenCounter, OpenAITokenCounter

from cafe.agents.llm import normalized_provider
from cafe.config import Settings

from . import storage
from .storage import (
    APP_MEMORY_METADATA,
    CART_ITEMS_TABLE,
    CARTS_TABLE,
    COMPRESSION_PROMPT,
    COMPRESSED_MARK,
    CONVERSATION_MESSAGES_TABLE,
    CONVERSATION_SUMMARIES_TABLE,
    CONVERSATIONS_TABLE,
    DEFAULT_USER_ID,
    MEMORY_SUMMARIES_TABLE,
    MENU_ITEMS_TABLE,
    ORDER_ITEMS_TABLE,
    ORDERS_TABLE,
    SUMMARY_MARK,
    SUMMARY_TEMPLATE,
    TOOL_CALL_MARK,
    TOOL_RESULT_MARK,
    TOOL_RESULT_MAX_CHARS,
    USERS_TABLE,
    AppSQLMemory,
    CafeConversationSummary,
    _normalize_async_database_url,
    _window_size,
    build_context,
    clear_cart_snapshot,
    compress_memory_after_turn,
    delete_session_data,
    ensure_menu_catalog,
    get_recent_messages,
    get_summary,
    list_conversation_messages,
    list_user_conversations,
    load_memory,
    resolve_menu_item_for_cart,
    save_cart_snapshot,
    save_messages,
    save_order_snapshot,
)
from .summaries import (
    MEMORY_SUMMARY_OVERLAP_MESSAGES,
    MemorySummaryDraft,
    get_latest_memory_summary,
    maybe_generate_memory_summary,
)


def make_token_counter(settings: Settings):
    provider = normalized_provider(settings)
    if provider in {"openai", "deepseek", "groq", "openrouter"}:
        return OpenAITokenCounter(settings.openai_model)
    return CharTokenCounter()


def make_chat_formatter(settings: Settings):
    kwargs = {
        "token_counter": make_token_counter(settings),
        "max_tokens": settings.memory_max_prompt_tokens,
    }
    provider = normalized_provider(settings)
    if provider == "anthropic":
        return AnthropicChatFormatter(**kwargs)
    if provider == "gemini":
        return GeminiChatFormatter(**kwargs)
    if provider == "ollama":
        return OllamaChatFormatter(**kwargs)
    if provider == "dashscope":
        return DashScopeChatFormatter(**kwargs)
    if provider == "deepseek":
        return DeepSeekChatFormatter(**kwargs)
    return OpenAIChatFormatter(**kwargs)


def make_multi_agent_formatter(settings: Settings):
    kwargs = {
        "token_counter": make_token_counter(settings),
        "max_tokens": settings.memory_max_prompt_tokens,
    }
    provider = normalized_provider(settings)
    if provider == "anthropic":
        return AnthropicMultiAgentFormatter(**kwargs)
    if provider == "gemini":
        return GeminiMultiAgentFormatter(**kwargs)
    if provider == "ollama":
        return OllamaMultiAgentFormatter(**kwargs)
    if provider == "dashscope":
        return DashScopeMultiAgentFormatter(**kwargs)
    if provider == "deepseek":
        return DeepSeekMultiAgentFormatter(**kwargs)
    return OpenAIMultiAgentFormatter(**kwargs)


def make_compression_config(settings: Settings) -> ReActAgent.CompressionConfig:
    return ReActAgent.CompressionConfig(
        enable=True,
        agent_token_counter=make_token_counter(settings),
        trigger_threshold=settings.memory_compression_trigger_tokens,
        keep_recent=_window_size(settings) + 1,
        compression_prompt=COMPRESSION_PROMPT,
        summary_template=SUMMARY_TEMPLATE,
        summary_schema=CafeConversationSummary,
    )


__all__ = [
    "APP_MEMORY_METADATA",
    "CART_ITEMS_TABLE",
    "CARTS_TABLE",
    "COMPRESSION_PROMPT",
    "COMPRESSED_MARK",
    "CONVERSATION_MESSAGES_TABLE",
    "CONVERSATION_SUMMARIES_TABLE",
    "CONVERSATIONS_TABLE",
    "DEFAULT_USER_ID",
    "MEMORY_SUMMARIES_TABLE",
    "MEMORY_SUMMARY_OVERLAP_MESSAGES",
    "MENU_ITEMS_TABLE",
    "ORDER_ITEMS_TABLE",
    "ORDERS_TABLE",
    "SUMMARY_MARK",
    "SUMMARY_TEMPLATE",
    "TOOL_CALL_MARK",
    "TOOL_RESULT_MARK",
    "TOOL_RESULT_MAX_CHARS",
    "USERS_TABLE",
    "AppSQLMemory",
    "CafeConversationSummary",
    "MemorySummaryDraft",
    "_normalize_async_database_url",
    "build_context",
    "clear_cart_snapshot",
    "compress_memory_after_turn",
    "delete_session_data",
    "ensure_menu_catalog",
    "get_latest_memory_summary",
    "get_recent_messages",
    "get_summary",
    "list_conversation_messages",
    "list_user_conversations",
    "load_memory",
    "maybe_generate_memory_summary",
    "make_chat_formatter",
    "make_compression_config",
    "make_multi_agent_formatter",
    "make_token_counter",
    "resolve_menu_item_for_cart",
    "save_cart_snapshot",
    "save_messages",
    "save_order_snapshot",
    "storage",
]
