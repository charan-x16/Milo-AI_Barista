"""Public memory API for AgentScope agents.

Storage/schema helpers live in ``storage.py``. This package initializer keeps
agent-facing formatter wiring separate from SQL persistence.
"""

from __future__ import annotations

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
    COMPRESSED_MARK,
    COMPRESSION_PROMPT,
    CONVERSATION_MESSAGES_TABLE,
    CONVERSATION_SUMMARIES_TABLE,
    CONVERSATIONS_TABLE,
    DEFAULT_USER_ID,
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
    SummaryCheckpoint,
    _normalize_async_database_url,
    _reset_storage_runtime_cache,
    build_context,
    clear_cart_snapshot,
    compress_memory_after_turn,
    delete_session_data,
    ensure_menu_catalog,
    ensure_storage_ready,
    get_recent_messages,
    get_summary,
    list_conversation_messages,
    list_user_conversations,
    load_memory,
    resolve_menu_item_for_cart,
    save_cart_snapshot,
    save_messages,
    save_order_snapshot,
    should_compress_memory_after_turn,
)


def make_token_counter(settings: Settings):
    """Handle make token counter.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return Any - The return value.
    """
    provider = normalized_provider(settings)
    if provider in {"openai", "deepseek", "groq", "openrouter"}:
        return OpenAITokenCounter(settings.openai_model)
    return CharTokenCounter()


def make_chat_formatter(settings: Settings):
    """Handle make chat formatter.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return Any - The return value.
    """
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
    """Handle make multi agent formatter.

    Args:
        - settings: Settings - The settings value.

    Returns:
        - return Any - The return value.
    """
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
    "SummaryCheckpoint",
    "_normalize_async_database_url",
    "_reset_storage_runtime_cache",
    "build_context",
    "clear_cart_snapshot",
    "compress_memory_after_turn",
    "delete_session_data",
    "ensure_menu_catalog",
    "ensure_storage_ready",
    "get_recent_messages",
    "get_summary",
    "list_conversation_messages",
    "list_user_conversations",
    "load_memory",
    "make_chat_formatter",
    "make_multi_agent_formatter",
    "make_token_counter",
    "resolve_menu_item_for_cart",
    "save_cart_snapshot",
    "save_messages",
    "save_order_snapshot",
    "should_compress_memory_after_turn",
    "storage",
]
